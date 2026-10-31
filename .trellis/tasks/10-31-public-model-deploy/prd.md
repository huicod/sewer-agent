# Public models and one-command setup

Make the existing suwewa/sewer-agent-models repository public at the user's explicit request. Only the existing seven runtime weights, their configs and source/license notices belong there. Check anonymous access against the exact release.

On a prepared Linux NVIDIA Docker host with the source checkout, one command should prompt for a single default DashScope Beijing API key, securely retain configuration, fetch and verify missing models by immutable revision and launch the complete stack. Reuse existing credentials, models and volumes on rerun. Custom providers retain explicit text and vision configurations. No auto-installation of GPU drivers or deployment to the running native service.

Starting point 8655abbd on dev/agent-v1. Main owns public visibility, repository pin, workflow, user guide, evidence and synchronization. Entry point, anonymous fetcher and license notices have separate file owners. Acceptance includes anonymous weight retrieval, checksum rejection and Docker with real weights on a fresh CI CPU host including container recreation. Native GPU evidence from the same seven weight digests remains separately documented.
