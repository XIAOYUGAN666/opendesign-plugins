"""Build identity embedded by the deterministic release builder."""

RELEASE_VERSION = "0.1.19"

# The release builder replaces this development sentinel in staged package input
# after calculating the canonical Connector build identity.
CONNECTOR_BUILD_ID = "sha256:" + "0" * 64
