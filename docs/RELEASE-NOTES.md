## Hermes Bridge Tool 0.2.1

This release brings the new sage-and-teal branding to the macOS app, setup window, README, and project previews. The Dock and Finder icon now has a softly shaded sage tile with transparent outer spacing. The menu bar uses a matching rounded tile and bridge silhouette, rendered as a native template for light and dark appearances, with connection status shown beside it.

### Install or upgrade

Download **hermes-bridge-tool-macos-universal.zip** for macOS 13 or later (Apple Silicon and Intel), or **hermes-bridge-tool-cli.tar.gz** for the CLI on Linux/macOS. Quit the older menu bar app before installing, extract the complete archive, and run **Install.command** on macOS or `sh install.sh` in the CLI bundle. Reopen the app from your user Applications folder to load the updated icon. Existing settings, credentials, and security policy are preserved.

If upgrading from 0.1.x, follow the [0.2.0 security upgrade instructions](https://github.com/hourafter4/hermes-bridge-tool/releases/tag/v0.2.0), including restarting older MCP clients, registering the stable launcher, and reconnecting. Monitor mode remains the default; this branding release does not grant task control or messaging permissions.

### Verify the downloads

The release includes both installation bundles, `hermes_bridge_tool-0.2.1-py3-none-any.whl`, the Python source distribution, and `SHA256SUMS`. Before extracting and executing an installer, verify its GitHub build provenance:

```sh
gh attestation verify hermes-bridge-tool-macos-universal.zip --repo hourafter4/hermes-bridge-tool
# Or for the CLI bundle:
gh attestation verify hermes-bridge-tool-cli.tar.gz --repo hourafter4/hermes-bridge-tool
```

Compare the reported source commit/tag with this release. The bundled installer verifies locked dependency hashes and preserves existing connection settings. Internet access may be needed for Python and dependency wheels. A raw wheel installation does not provide the bundle’s locked-dependency guarantees.

**The macOS release is ad hoc signed and is not Apple-notarized.** The archive includes its actual signing status in `SIGNING-STATUS.txt`. See the [release verification guide](https://github.com/hourafter4/hermes-bridge-tool/blob/v0.2.1/docs/RELEASING.md) and [connection setup](https://github.com/hourafter4/hermes-bridge-tool/blob/v0.2.1/docs/SETUP.md).
