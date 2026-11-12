# Public connector on Google Cloud Run

The public connector is the Noul server in **public, read-only mode** (`NOUL_PUBLIC=1`). Anyone can use it by
pasting the URL `https://<service>/mcp` into Claude.ai, Claude Desktop (Connectors) or ChatGPT (developer mode),
with nothing to install.

- **What it exposes:** 12 tools from the `research` profile. Tools that write files and tools that list or read
  dossiers saved on the server are left out. There are no MCP resources and no login.
- **Transport:** stateless Streamable HTTP with JSON responses. There are no sessions and no long SSE streams, so
  any instance can serve any request.
- **Limits:**
  - **tool calls per instance:** 300/min, with a burst of 60. This is the cost control for the upstream APIs; when
    it is exceeded, the model receives `RateLimited: …` and knows to wait;
  - **concurrent calls:** at most 8 per instance;
  - **per-client flooding:** a guard of 600 requests/min per IP (IPv6 grouped by /64). Hosted clients (Claude.ai,
    ChatGPT) share egress IPs, so this is not the main limit;
  - **per-call caps, announced in the schema:** 25 results per search and 5 full texts per question. Lists above
    the cap are refused, and numbers above it are reduced with a `notice`.
- **Memory:** the SQLite cache lives in `/tmp`, which on Cloud Run uses the instance's RAM. It is therefore limited
  to 2,000 entries (`NOUL_CACHE_MAX_ROWS`), and downloads are limited to 15 MB (`NOUL_MAX_DOWNLOAD_MB`).
- **Cost:** it scales to zero (`--min-instances 0`), so with no traffic the cost is close to nothing.
  `--max-instances` caps the maximum spend.
- **Data:** the server stores no user data.

Deployment is done by the workflow `.github/workflows/deploy-connector.yml`, triggered manually or by every `v*`
tag. It runs in the `production` environment, which requires approval, and authenticates through **Workload Identity
Federation**, with no JSON keys. The setup below is done **once**.

## 1. Project and APIs

```bash
export PROJECT_ID=noul-connector          # pick a unique ID
export REGION=us-central1                 # or southamerica-east1 (São Paulo)
export GITHUB_REPO=ernaneDomingues/noul-search-agent
export GITHUB_REPO_ID=1434740215           # gh api repos/$GITHUB_REPO -q .id
export GITHUB_OWNER_ID=112431431           # gh api repos/$GITHUB_REPO -q .owner.id

gcloud projects create "$PROJECT_ID"      # or use an existing project
gcloud billing projects link "$PROJECT_ID" --billing-account=<BILLING-ACCOUNT-ID>
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com secretmanager.googleapis.com
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
```

## 2. Image repository

```bash
gcloud artifacts repositories create noul --repository-format=docker --location="$REGION"
```

## 3. Service accounts with minimal permissions

The deployer gets **no** broad roles on the project. It can only:

- push images to the `noul` repository;
- deploy Cloud Run services (`run.developer`, which cannot change IAM);
- act as the `noul-runtime` account, and no other.

```bash
gcloud iam service-accounts create noul-deployer --display-name="Noul deployer"
gcloud iam service-accounts create noul-runtime --display-name="Noul connector runtime"
DEPLOYER="noul-deployer@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME="noul-runtime@$PROJECT_ID.iam.gserviceaccount.com"

gcloud artifacts repositories add-iam-policy-binding noul --location="$REGION" \
  --member="serviceAccount:$DEPLOYER" --role=roles/artifactregistry.writer
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOYER" --role=roles/run.developer
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" \
  --member="serviceAccount:$DEPLOYER" --role=roles/iam.serviceAccountUser
```

## 4. Workload Identity Federation (GitHub → GCP, no keys)

The condition only issues credentials to:

- this repository and this owner, identified by numeric ID, which another account cannot reuse;
- **this workflow**;
- the `production` environment;
- the `master` branch or `v*` tags.

```bash
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc noul-repo \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository_id == '$GITHUB_REPO_ID' && assertion.repository_owner_id == '$GITHUB_OWNER_ID' && assertion.workflow_ref.startsWith('$GITHUB_REPO/.github/workflows/deploy-connector.yml@') && assertion.environment == 'production' && (assertion.ref == 'refs/heads/master' || assertion.ref.startsWith('refs/tags/v'))"

gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO"
```

## 5. Secrets (optional, but recommended)

A contact e-mail and API keys improve the quotas, because the connector shares one IP among all its users. Create
only the ones you have. Also set **alerts or quotas** for the keys in each API's dashboard.

```bash
printf 'you@university.edu' | gcloud secrets create noul-email --data-file=-
printf '<key>' | gcloud secrets create s2-api-key --data-file=-          # Semantic Scholar
printf '<key>' | gcloud secrets create openalex-api-key --data-file=-    # OpenAlex
printf '<key>' | gcloud secrets create ncbi-api-key --data-file=-        # PubMed / NCBI
for s in noul-email s2-api-key openalex-api-key ncbi-api-key; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$RUNTIME" \
    --role=roles/secretmanager.secretAccessor 2>/dev/null
done
```

## 6. GitHub: environment, tag protection and variables

```bash
# `production` environment that requires your approval for each deploy
gh api -X PUT "repos/$GITHUB_REPO/environments/production" \
  -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)"
# only administrators create v* tags (every v* tag triggers a deploy)
gh api -X POST "repos/$GITHUB_REPO/rulesets" --input - <<'JSON'
{"name": "release tags", "target": "tag", "enforcement": "active",
 "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
 "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
 "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]}
JSON

gh variable set GCP_PROJECT_ID --body "$PROJECT_ID"
gh variable set GCP_REGION --body "$REGION"
gh variable set GCP_WIF_PROVIDER --body "projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/noul-repo"
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --body "$DEPLOYER"
gh variable set GCP_RUNTIME_SERVICE_ACCOUNT --body "$RUNTIME"
# only the secrets you created in step 5:
gh variable set GCP_RUN_SECRETS --body "NOUL_EMAIL=noul-email:latest,S2_API_KEY=s2-api-key:latest,OPENALEX_API_KEY=openalex-api-key:latest,NCBI_API_KEY=ncbi-api-key:latest"
# optional: GCP_SERVICE_NAME (default noul-connector), GCP_ARTIFACT_REPO (default noul), GCP_MAX_INSTANCES (default 3)
```

These are variables, not secrets: none of the values is confidential, because access is controlled by Workload
Identity and by the environment.

## 7. Deploy

```bash
gh workflow run "Deploy public connector (Cloud Run)" --ref master
gh run watch          # approve the deploy when GitHub asks (production environment)
```

## 8. Public access (once, after the first deploy)

The deployer cannot change IAM. Make the service public once, with your own account:

```bash
gcloud run services add-iam-policy-binding noul-connector --region "$REGION" \
  --member=allUsers --role=roles/run.invoker
URL=$(gcloud run services describe noul-connector --region "$REGION" --format='value(status.url)')
echo "$URL/mcp"
```

Write down the MCP URL: it goes into the site's install page (`connector_url` in `site/config.json`, then run
`python scripts/build_site.py`).

To check:

```bash
curl -s "$URL/healthz"                                                              # {"status":"ok",...}
curl -s "$URL/tools" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["tools"]))'   # 12
curl -s -o /dev/null -w '%{http_code}\n' "$URL/mcp"                                 # 405 (POST only)
```

## 9. Add the connector

- **Claude.ai / Claude Desktop:** Settings → Connectors → Add custom connector → URL `$URL/mcp`, no
  authentication. Free plans limit the number of custom connectors; on Team/Enterprise an administrator adds it.
- **ChatGPT:** Settings → Apps & Connectors → Advanced settings → Developer mode → Create, with the URL `$URL/mcp`
  and "No authentication". Developer mode depends on the plan. Every tool is `readOnlyHint`, so none asks for a write
  confirmation.

## Proxy, domain and client IP

- On the `*.run.app` URL and on domain mappings, Google's front end appends the real client IP as the **last**
  entry of `X-Forwarded-For`, hence `NOUL_TRUSTED_PROXY=1`.
- Behind an external **Application Load Balancer** (needed for Cloud Armor, or where domain mapping is not
  available), the header becomes `<client>, <load-balancer-ip>`. In that case use `NOUL_TRUSTED_PROXY=2`.
- **Custom domain:** `gcloud beta run domain-mappings create --service noul-connector --domain mcp.yourdomain.org
  --region "$REGION"`. The command is in preview and is not available in every region; the alternative is an ALB with
  a serverless NEG.

## Operations

- **Logs:** `gcloud run services logs read noul-connector --region "$REGION"`. By default only warnings appear.
  With `NOUL_LOG_LEVEL=INFO` the tool names appear, never arguments or e-mails.
- **Tuning** (service variables; the deploy uses `--update-env-vars` and keeps what you set):
  - `NOUL_TOOL_RATE_LIMIT` and `NOUL_TOOL_RATE_BURST`: tool calls per instance;
  - `NOUL_MAX_CONCURRENCY`;
  - `NOUL_RATE_LIMIT` and `NOUL_RATE_BURST`: per-client flood guard;
  - `NOUL_CACHE_MAX_ROWS` and `NOUL_MAX_DOWNLOAD_MB`.

  Example: `gcloud run services update noul-connector --region "$REGION" --update-env-vars NOUL_TOOL_RATE_LIMIT=120`.
- **Shut the connector down:** `gcloud run services delete noul-connector --region "$REGION"`.
