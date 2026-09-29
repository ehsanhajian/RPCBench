"""Yellowstone Geyser gRPC slots subscribe (optional deps).

Requires: pip install 'rpcbench[yellowstone]'
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

from rpcbench.yellowstone import SlotEvent

# Minimal subset of yellowstone-grpc geyser.proto field numbers.
# https://github.com/rpcpool/yellowstone-grpc/blob/master/yellowstone-grpc-proto/proto/geyser.proto
_GRPC_SERVICE = "geyser.Geyser"
_GRPC_METHOD = "Subscribe"
_SLOT_STATUS = {
    0: "processed",
    1: "confirmed",
    2: "finalized",
}


def subscribe_slots(
    url: str,
    *,
    headers: tuple[tuple[str, str], ...] = (),
    timeout: float = 10.0,
    stop_event: threading.Event | None = None,
) -> Iterator[SlotEvent]:
    """Bidirectional Subscribe stream filtered to slot updates."""
    import grpc
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    Request, Filter, Update = _message_classes(descriptor_pb2, descriptor_pool, message_factory)
    request = Request()
    request.slots["client"].CopyFrom(Filter())

    target, use_tls = _grpc_target(url)
    channel = _open_channel(target, use_tls=use_tls)
    stop = stop_event or threading.Event()
    meta = tuple((str(k), str(v)) for k, v in headers)

    try:
        stub = channel.stream_stream(
            f"/{_GRPC_SERVICE}/{_GRPC_METHOD}",
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=Update.FromString,
        )

        def requests() -> Iterator[Any]:
            yield request
            stop.wait()

        call = stub(requests(), timeout=timeout, metadata=meta or None)
        for update in call:
            if stop.is_set():
                break
            slot_msg = _slot_payload(update)
            if slot_msg is None:
                continue
            status_raw = int(getattr(slot_msg, "status", 0))
            yield SlotEvent(
                slot=int(slot_msg.slot),
                status=_SLOT_STATUS.get(status_raw, str(status_raw)),
                t_mono=time.monotonic(),
            )
    finally:
        stop.set()
        channel.close()


def _message_classes(descriptor_pb2, descriptor_pool, message_factory):
    pool = descriptor_pool.DescriptorPool()
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "geyser_min.proto"
    file_proto.package = "geyser"
    file_proto.syntax = "proto3"

    filter_msg = file_proto.message_type.add()
    filter_msg.name = "SubscribeRequestFilterSlots"
    flag = filter_msg.field.add()
    flag.name = "filter_by_commitment"
    flag.number = 1
    flag.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    flag.type = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

    entry = file_proto.message_type.add()
    entry.name = "SubscribeRequest.SlotsEntry"
    entry.options.map_entry = True
    key = entry.field.add()
    key.name = "key"
    key.number = 1
    key.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    key.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    value = entry.field.add()
    value.name = "value"
    value.number = 2
    value.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    value.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    value.type_name = ".geyser.SubscribeRequestFilterSlots"

    req_msg = file_proto.message_type.add()
    req_msg.name = "SubscribeRequest"
    slots = req_msg.field.add()
    slots.name = "slots"
    slots.number = 1
    slots.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    slots.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    slots.type_name = ".geyser.SubscribeRequest.SlotsEntry"

    slot_msg = file_proto.message_type.add()
    slot_msg.name = "SubscribeUpdateSlot"
    for name, number, typ in (
        ("slot", 1, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64),
        ("parent", 2, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64),
        ("status", 3, descriptor_pb2.FieldDescriptorProto.TYPE_INT32),
    ):
        field = slot_msg.field.add()
        field.name = name
        field.number = number
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = typ

    upd_msg = file_proto.message_type.add()
    upd_msg.name = "SubscribeUpdate"
    slot_field = upd_msg.field.add()
    slot_field.name = "slot"
    slot_field.number = 2
    slot_field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    slot_field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    slot_field.type_name = ".geyser.SubscribeUpdateSlot"

    pool.Add(file_proto)
    try:
        from google.protobuf.message_factory import GetMessageClassesForFiles

        classes = GetMessageClassesForFiles([file_proto.name], pool)
        return (
            classes["geyser.SubscribeRequest"],
            classes["geyser.SubscribeRequestFilterSlots"],
            classes["geyser.SubscribeUpdate"],
        )
    except Exception:
        get = message_factory.GetMessageClass
        return (
            get(pool.FindMessageTypeByName("geyser.SubscribeRequest")),
            get(pool.FindMessageTypeByName("geyser.SubscribeRequestFilterSlots")),
            get(pool.FindMessageTypeByName("geyser.SubscribeUpdate")),
        )


def _slot_payload(update: Any) -> Any | None:
    for field, value in update.ListFields():
        if field.name == "slot":
            return value
    return None


def _grpc_target(url: str) -> tuple[str, bool]:
    text = url.strip()
    if "://" not in text:
        return text, True
    parts = urlsplit(text)
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"invalid gRPC URL: {url!r}")
    port = parts.port
    scheme = (parts.scheme or "").lower()
    if scheme in {"grpc", "http"}:
        return f"{host}:{port or 80}", False
    if scheme in {"grpcs", "https"}:
        return f"{host}:{port or 443}", True
    raise ValueError(f"gRPC URL must be grpc(s)/http(s), got {scheme!r}")


def _open_channel(target: str, *, use_tls: bool):
    import grpc

    options = (
        ("grpc.primary_user_agent", "rpcbench"),
        ("grpc.keepalive_time_ms", 10_000),
    )
    if use_tls:
        return grpc.secure_channel(target, grpc.ssl_channel_credentials(), options=options)
    return grpc.insecure_channel(target, options=options)
