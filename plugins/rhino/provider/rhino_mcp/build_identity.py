"""Build identity embedded by the deterministic release builder."""

RELEASE_VERSION = "0.1.16"

# The release builder replaces this development sentinel in staged package input
# after calculating the canonical Provider build identity.
PROVIDER_BUILD_ID = "sha256:" + "0" * 64
