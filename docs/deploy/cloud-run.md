# Conector público no Google Cloud Run

O conector público é o servidor Noul em **modo público e só de leitura** (`NOUL_PUBLIC=1`). Qualquer pessoa pode usá-lo
colando a URL `https://<serviço>/mcp` no Claude.ai, no Claude Desktop (Conectores) ou no ChatGPT (modo
desenvolvedor), sem instalar nada.

- **O que expõe:** 12 ferramentas do perfil `research`. Ficam de fora as ferramentas que gravam arquivos e as que listam
  ou leem dossiês salvos no servidor. Não há recursos MCP nem login.
- **Transporte:** Streamable HTTP *stateless* com respostas JSON. Não há sessões nem streams SSE longos, então qualquer
  instância atende qualquer requisição.
- **Limites:**
  - **chamadas de ferramenta por instância:** 300/min, com rajada de 60. É o controle de custo das APIs; quando estoura,
    o modelo recebe `RateLimited: …` e sabe esperar;
  - **chamadas simultâneas:** no máximo 8 por instância;
  - **inundação por cliente:** proteção de 600 requisições/min por IP (IPv6 agrupado por /64). Os clientes hospedados
    (Claude.ai, ChatGPT) compartilham IPs de saída, então esse não é o limite principal;
  - **tetos por chamada, anunciados no schema:** 25 resultados por busca e 5 textos completos por pergunta. Listas acima
    do teto são recusadas, e números acima dele são reduzidos com um aviso (`notice`).
- **Memória:** o cache SQLite fica em `/tmp`, que no Cloud Run consome a RAM da instância. Por isso ele é limitado a
  2.000 entradas (`NOUL_CACHE_MAX_ROWS`), e os downloads ficam limitados a 15 MB (`NOUL_MAX_DOWNLOAD_MB`).
- **Custo:** escala a zero (`--min-instances 0`), então sem uso o custo é praticamente nulo. `--max-instances` limita o
  gasto máximo.
- **Dados:** o servidor não guarda dados de usuários.

O deploy é feito pelo workflow `.github/workflows/deploy-connector.yml`, disparado manualmente ou a cada tag `v*`. Ele
roda no *environment* `production`, que exige aprovação, e se autentica por **Workload Identity Federation**, sem chaves
JSON. A configuração abaixo é feita **uma única vez**.

## 1. Projeto e APIs

```bash
export PROJECT_ID=noul-connector          # escolha um ID único
export REGION=us-central1                 # ou southamerica-east1 (São Paulo)
export GITHUB_REPO=ernaneDomingues/noul-search-agent
export GITHUB_REPO_ID=1434740215           # gh api repos/$GITHUB_REPO -q .id
export GITHUB_OWNER_ID=112431431           # gh api repos/$GITHUB_REPO -q .owner.id

gcloud projects create "$PROJECT_ID"      # ou use um projeto existente
gcloud billing projects link "$PROJECT_ID" --billing-account=<ID-DA-CONTA-DE-FATURAMENTO>
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com secretmanager.googleapis.com
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
```

## 2. Repositório de imagens

```bash
gcloud artifacts repositories create noul --repository-format=docker --location="$REGION"
```

## 3. Contas de serviço com permissões mínimas

O deployer **não** recebe papéis amplos no projeto. Ele só pode:

- enviar imagens para o repositório `noul`;
- implantar serviços Cloud Run (`run.developer`, que não altera IAM);
- agir como a conta `noul-runtime`, e nenhuma outra.

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

## 4. Workload Identity Federation (GitHub → GCP, sem chaves)

A condição só emite credenciais para:

- este repositório e este dono, identificados por ID numérico, que não pode ser reutilizado por outra conta;
- **este workflow**;
- o *environment* `production`;
- o branch `master` ou tags `v*`.

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

## 5. Segredos (opcionais, mas recomendados)

Um e-mail de contato e chaves de API melhoram as cotas, porque o conector compartilha um IP entre todos os usuários.
Crie apenas os que tiver. Configure também, nos painéis de cada API, **alertas ou cotas** para as chaves.

```bash
printf 'voce@universidade.br' | gcloud secrets create noul-email --data-file=-
printf '<chave>' | gcloud secrets create s2-api-key --data-file=-          # Semantic Scholar
printf '<chave>' | gcloud secrets create openalex-api-key --data-file=-    # OpenAlex
printf '<chave>' | gcloud secrets create ncbi-api-key --data-file=-        # PubMed / NCBI
for s in noul-email s2-api-key openalex-api-key ncbi-api-key; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$RUNTIME" \
    --role=roles/secretmanager.secretAccessor 2>/dev/null
done
```

## 6. GitHub: *environment*, proteção de tags e variáveis

```bash
# environment `production` exigindo sua aprovação em cada deploy
gh api -X PUT "repos/$GITHUB_REPO/environments/production" \
  -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)"
# só administradores criam tags v* (cada tag v* dispara um deploy)
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
# só os segredos que você criou no passo 5:
gh variable set GCP_RUN_SECRETS --body "NOUL_EMAIL=noul-email:latest,S2_API_KEY=s2-api-key:latest,OPENALEX_API_KEY=openalex-api-key:latest,NCBI_API_KEY=ncbi-api-key:latest"
# opcionais: GCP_SERVICE_NAME (padrão noul-connector), GCP_ARTIFACT_REPO (padrão noul), GCP_MAX_INSTANCES (padrão 3)
```

Essas são *variables*, não *secrets*: nenhum valor é sigiloso, porque o acesso é controlado pelo Workload Identity e
pelo *environment*.

## 7. Deploy

```bash
gh workflow run "Deploy public connector (Cloud Run)" --ref master
gh run watch          # aprove o deploy quando o GitHub pedir (environment production)
```

## 8. Acesso público (uma única vez, após o primeiro deploy)

O deployer não altera IAM. Torne o serviço público uma vez, com a sua conta:

```bash
gcloud run services add-iam-policy-binding noul-connector --region "$REGION" \
  --member=allUsers --role=roles/run.invoker
URL=$(gcloud run services describe noul-connector --region "$REGION" --format='value(status.url)')
echo "$URL/mcp"
```

Anote a URL do MCP: ela vai na página "Instalar" do site (`site/config.json`, Fase 2b-2).

Para conferir:

```bash
curl -s "$URL/healthz"                                                              # {"status":"ok",...}
curl -s "$URL/tools" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["tools"]))'   # 12
curl -s -o /dev/null -w '%{http_code}\n' "$URL/mcp"                                 # 405 (só POST)
```

## 9. Adicionar o conector

- **Claude.ai / Claude Desktop:** Configurações → Conectores → Adicionar conector personalizado → URL `$URL/mcp`, sem
  autenticação. Nos planos gratuitos há limite de conectores personalizados; em Team/Enterprise, quem adiciona é um
  administrador.
- **ChatGPT:** Settings → Apps & Connectors → Advanced settings → Developer mode → Create, com a URL `$URL/mcp` e
  "No authentication". O modo desenvolvedor depende do plano. As ferramentas são todas `readOnlyHint`, então não pedem
  confirmação de escrita.

## Proxy, domínio e IP do cliente

- Na URL `*.run.app` e nos *domain mappings*, o front-end do Google acrescenta o IP real do cliente como **última**
  entrada do `X-Forwarded-For`, por isso `NOUL_TRUSTED_PROXY=1`.
- Atrás de um **Application Load Balancer** externo (necessário para Cloud Armor, ou onde não há *domain mapping*), o
  cabeçalho vira `<cliente>, <ip-do-balanceador>`. Nesse caso, use `NOUL_TRUSTED_PROXY=2`.
- **Domínio próprio:** `gcloud beta run domain-mappings create --service noul-connector --domain mcp.seudominio.org
  --region "$REGION"`. O comando está em *preview* e não existe em todas as regiões; a alternativa é um ALB com NEG
  serverless.

## Operação

- **Logs:** `gcloud run services logs read noul-connector --region "$REGION"`. Por padrão só aparecem avisos. Com
  `NOUL_LOG_LEVEL=INFO`, aparecem os nomes das ferramentas, nunca argumentos nem e-mails.
- **Ajustes** (variáveis do serviço; o deploy usa `--update-env-vars` e preserva o que você definir):
  - `NOUL_TOOL_RATE_LIMIT` e `NOUL_TOOL_RATE_BURST`: chamadas de ferramenta por instância;
  - `NOUL_MAX_CONCURRENCY`;
  - `NOUL_RATE_LIMIT` e `NOUL_RATE_BURST`: proteção contra inundação por cliente;
  - `NOUL_CACHE_MAX_ROWS` e `NOUL_MAX_DOWNLOAD_MB`.

  Exemplo: `gcloud run services update noul-connector --region "$REGION" --update-env-vars NOUL_TOOL_RATE_LIMIT=120`.
- **Desligar o conector:** `gcloud run services delete noul-connector --region "$REGION"`.
