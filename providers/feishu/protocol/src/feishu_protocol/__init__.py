"""Private contracts shared by Workspace Feishu deployable components."""

from feishu_protocol.capabilities import (
    DOCX_MEDIA_READ_CAPABILITY,
    DOCX_READ_CAPABILITY,
    SHEETS_READ_CAPABILITY,
    SUPPORTED_CAPABILITIES,
    WIKI_NODE_READ_CAPABILITY,
)
from feishu_protocol.client import LocalClientIdentity
from feishu_protocol.leases import LeaseDelivery, LeaseRequest

__all__ = [
    "DOCX_MEDIA_READ_CAPABILITY",
    "DOCX_READ_CAPABILITY",
    "LeaseDelivery",
    "LeaseRequest",
    "LocalClientIdentity",
    "SHEETS_READ_CAPABILITY",
    "SUPPORTED_CAPABILITIES",
    "WIKI_NODE_READ_CAPABILITY",
]
