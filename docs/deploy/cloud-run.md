# Conector público no Google Cloud Run

O conector público é o servidor Noul em **modo público e só de leitura** (`NOUL_PUBLIC=1`). Qualquer pessoa pode usá-lo
colando a URL `https://<serviço>/mcp` no Claude.ai, no Claude Desktop (Conectores) ou no ChatGPT (modo
desenvolvedor), sem instalar nada.

- **O que expõe:** só o perfil `research` (14 ferramentas), sem ferramentas que gravam arquivos e sem os dossiês salvos
  no servidor.
- **Limites:** 60 requisições/min por IP (rajada de 20), no máximo 8 chamadas de ferramenta simultâneas por instância
  e tetos por chamada (ex.: 25 resultados por busca, 5 textos completos por pergunta).
- **Custo:** escala a zero (`--min-instances 0`), então sem uso o custo é praticamente nulo. `--max-instances` limita
  o gasto máximo.
- **Dados:** o servidor não guarda dados de usuários. O cache (SQLite) fica em `/tmp` de cada instância e some quando
  ela é desligada.

O deploy é feito pelo workflow `.github/workflows/deploy-connector.yml`, disparado manualmente ou a cada tag `v*`. A
autenticação usa **Workload Identity Federation**, sem chaves JSON. A configuração abaixo é feita **uma única vez**.

## 1. Projeto e APIs

```bash
export PROJECT_ID=noul-connector          # escolha um ID único
export REGION=us-central1                 # ou southamerica-east1 (São Paulo)
export GITHUB_REPO=ernaneDomingues/noul-search-agent

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

## 3. Contas de serviço

```bash
# quem faz o deploy (usada pelo GitHub Actions)
gcloud iam service-accounts create noul-deployer --display-name="Noul deployer"
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:noul-deployer@$PROJECT_ID.iam.gserviceaccount.com" --role="$role"
done

# com quem o serviço roda (sem permissões além de ler os próprios segredos)
gcloud iam service-accounts create noul-runtime --display-name="Noul connector runtime"
```

## 4. Workload Identity Federation (GitHub → GCP, sem chaves)

```bash
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc noul-repo \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '$GITHUB_REPO'"

gcloud iam service-accounts add-iam-policy-binding \
  "noul-deployer@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO"
```

## 5. Segredos (opcionais, mas recomendados)

Um e-mail de contato e chaves de API melhoram as cotas, porque o conector compartilha um IP entre todos os usuários.
Crie apenas os que tiver:

```bash
printf 'voce@universidade.br' | gcloud secrets create noul-email --data-file=-
printf '<chave>' | gcloud secrets create s2-api-key --data-file=-          # Semantic Scholar
printf '<chave>' | gcloud secrets create openalex-api-key --data-file=-    # OpenAlex
printf '<chave>' | gcloud secrets create ncbi-api-key --data-file=-        # PubMed / NCBI
for s in noul-email s2-api-key openalex-api-key ncbi-api-key; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:noul-runtime@$PROJECT_ID.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor 2>/dev/null
done
```

## 6. Variáveis do repositório no GitHub

```bash
gh variable set GCP_PROJECT_ID --body "$PROJECT_ID"
gh variable set GCP_REGION --body "$REGION"
gh variable set GCP_WIF_PROVIDER --body "projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/noul-repo"
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --body "noul-deployer@$PROJECT_ID.iam.gserviceaccount.com"
gh variable set GCP_RUNTIME_SERVICE_ACCOUNT --body "noul-runtime@$PROJECT_ID.iam.gserviceaccount.com"
# só os segredos que você criou no passo 5:
gh variable set GCP_RUN_SECRETS --body "NOUL_EMAIL=noul-email:latest,S2_API_KEY=s2-api-key:latest,OPENALEX_API_KEY=openalex-api-key:latest,NCBI_API_KEY=ncbi-api-key:latest"
# opcionais: GCP_SERVICE_NAME (padrão noul-connector), GCP_ARTIFACT_REPO (padrão noul), GCP_MAX_INSTANCES (padrão 3)
```

Essas são *variables*, não *secrets*: nenhum valor é sigiloso, porque o acesso é controlado pelo Workload Identity.

## 7. Deploy

```bash
gh workflow run "Deploy public connector (Cloud Run)"
gh run watch
```

O resumo da execução mostra a URL do MCP (`https://noul-connector-….run.app/mcp`). Anote-a: ela vai na página
"Instalar" do site (`site/config.json`, Fase 2b-2).

## 8. Conferir

```bash
URL=https://noul-connector-….run.app
curl -s "$URL/healthz"                                  # {"status":"ok",...}
curl -s "$URL/tools" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["tools"]))'   # 14
for i in $(seq 1 30); do curl -s -o /dev/null -w '%{http_code} ' "$URL/tools"; done; echo   # 200... e depois 429
```

- **Claude.ai / Claude Desktop:** Configurações → Conectores → Adicionar conector personalizado → URL `$URL/mcp`, sem
  autenticação.
- **ChatGPT:** Settings → Apps → Advanced → modo desenvolvedor → criar app com a URL `$URL/mcp` e
  "No authentication".

## Domínio próprio (opcional)

```bash
gcloud beta run domain-mappings create --service noul-connector --domain mcp.seudominio.org --region "$REGION"
```

Depois, crie no seu DNS os registros que o comando indicar.

## Operação

- **Logs:** `gcloud run services logs read noul-connector --region "$REGION"`. Por padrão só aparecem avisos; para
  mais detalhes, defina `NOUL_LOG_LEVEL=INFO`, que registra os nomes das ferramentas, nunca argumentos nem e-mails.
- **Ajustes de limite** (variáveis de ambiente do serviço): `NOUL_RATE_LIMIT` (req/min por IP; `0` desliga),
  `NOUL_RATE_BURST` e `NOUL_MAX_CONCURRENCY`.
- **Desligar o conector:** `gcloud run services delete noul-connector --region "$REGION"`.
