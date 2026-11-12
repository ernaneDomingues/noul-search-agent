# Release: signing, protection and verification

Noul's executables and extensions are published **unsigned** until certificates exist. Without a signature:

- **Windows:** SmartScreen shows "Windows protected your PC". To run the file, click **More info → Run anyway**.
- **macOS:** Gatekeeper blocks the executable the first time. Since macOS 15, "right-click → Open" no longer
  clears it; go to System Settings → Privacy & Security → **Open Anyway**. In a terminal:
  `xattr -d com.apple.quarantine ./noul`.
- **macOS and Linux:** the executables ship as `.tar.gz` (`noul-macos-arm64.tar.gz`, `noul-linux-x64.tar.gz`),
  because a browser does not keep the execute permission of a bare file.
- **Claude Desktop (.mcpb):** the universal variant (`noul.mcpb`, type `uv`) contains no binary. That makes it the
  recommended one on macOS while there is no signature.

## How the pipeline protects itself

The workflow `.github/workflows/release.yml` separates the stages:

1. **build:** builds the executable from `uv.lock` and `packaging/pyinstaller/requirements.txt`, both with
   mandatory hashes. It has no access to any secret.
2. **sign-macos / sign-windows:** only download the executable and sign it, without running project code. They run
   only on a `v*` tag push, when the variable `SIGNING_ENABLED` is `true`, inside the `release` environment, which
   holds the secrets and requires approval. They never run on pull requests or manual runs.
3. **package:** smoke-tests the final executable and packs the `.mcpb` files with the packer pinned in
   `packaging/mcpb/package-lock.json` (`npm ci`).
4. **publish:** also runs in the `release` environment, only on a tag push. It generates `SHA256SUMS` and the
   **provenance attestation** and creates the release. It refuses to overwrite an existing release, and tags with a
   `-` (e.g. `v2.0.0-rc1`) become pre-releases.

One-time setup (repository → Settings):

```bash
GITHUB_REPO=ernaneDomingues/noul-search-agent
# `release` environment that requires your approval (signing and publishing)
gh api -X PUT "repos/$GITHUB_REPO/environments/release" \
  -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)" \
  -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true"
gh api -X POST "repos/$GITHUB_REPO/environments/release/deployment-branch-policies" -f name='v*' -f type=tag
```

- **Protect the `v*` tags** with the ruleset described in `docs/deploy/cloud-run.md` (step 6), so that only
  administrators can create, move or delete version tags.
- **Turn on "Release immutability"** (Settings → General → Releases), so that the files of a published release
  cannot be replaced later.

## Verify a downloaded file

- **`SHA256SUMS`** only detects a corrupted download: it lives in the same release as the files.
- **Attestation:** to confirm that the file was built by this repository, by this workflow, run:

  ```bash
  gh attestation verify noul-windows-x64.exe --repo ernaneDomingues/noul-search-agent
  ```

## Turn on signing

Create the secrets **in the `release` environment** (Settings → Environments → release → Environment secrets),
never as repository secrets. Then:

```bash
gh variable set SIGNING_ENABLED --body true
```

### macOS (Apple Developer Program, US$ 99/year)

| Secret | Content |
|---|---|
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_CERTIFICATE_P12_BASE64` | *Developer ID Application* certificate exported as .p12, in base64 (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | password of the .p12 |
| `APPLE_ID`, `APPLE_TEAM_ID` | Apple account and team |
| `APPLE_APP_PASSWORD` | app-specific password for notarization (appleid.apple.com) |

The job signs with `codesign --options runtime` and notarizes with `notarytool`:

- the certificate goes into a temporary keychain, with a random password and usage restricted to `codesign`, which
  is deleted at the end even on failure;
- the entitlements `disable-library-validation` and `allow-unsigned-executable-memory` are needed because the
  onefile executable extracts Python at run time.

### Windows (Azure Artifact Signing, formerly Trusted Signing)

| Secret | Content |
|---|---|
| `AZURE_TRUSTED_SIGNING_ENDPOINT` | e.g. `https://eus.codesigning.azure.net/` |
| `AZURE_TRUSTED_SIGNING_ACCOUNT`, `AZURE_CERTIFICATE_PROFILE` | signing account and certificate profile |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | app registration with the *Trusted Signing Certificate Profile Signer* role |

An alternative is a traditional OV/EV certificate, which would mean replacing the step with `signtool`.

## The `uv` extension and the lockfile

The `noul.mcpb` variant ships `uv.lock`, and the manifest's `mcp_config` uses `uv run --frozen`. An application that
manages `uv` on its own runs `uv run --directory <extension> noul_server.py` instead, and may not pass `--frozen`.
In that case the dependency versions are resolved from the bounds in `pyproject.toml`.
