# Release: assinatura, proteção e verificação

Os executáveis e extensões do Noul são publicados **sem assinatura** até existirem certificados. Sem assinatura:

- **Windows:** o SmartScreen mostra "O Windows protegeu o computador". Para executar, clique em **Mais informações →
  Executar assim mesmo**.
- **macOS:** o Gatekeeper bloqueia o executável na primeira execução. Desde o macOS 15, o atalho "botão direito →
  Abrir" não libera mais; vá em Ajustes do Sistema → Privacidade e Segurança → **Abrir Mesmo Assim**. No terminal:
  `xattr -d com.apple.quarantine ./noul`.
- **macOS e Linux:** os executáveis saem em `.tar.gz` (`noul-macos-arm64.tar.gz`, `noul-linux-x64.tar.gz`), porque o
  navegador não preserva a permissão de execução de um arquivo solto.
- **Claude Desktop (.mcpb):** a variante universal (`noul.mcpb`, tipo `uv`) não contém binário. Por isso é a
  recomendada no macOS enquanto não houver assinatura.

## Como o pipeline se protege

O workflow `.github/workflows/release.yml` separa as etapas:

1. **build:** monta o executável a partir do `uv.lock` e de `packaging/pyinstaller/requirements.txt`, ambos com
   hashes obrigatórios. Não tem acesso a nenhum segredo.
2. **sign-macos / sign-windows:** só baixam o executável e o assinam, sem rodar código do projeto. Rodam apenas em
   push de tag `v*`, quando a variável `SIGNING_ENABLED` vale `true`, dentro do *environment* `release`, que guarda
   os segredos e exige aprovação. Nunca rodam em pull requests nem em execuções manuais.
3. **package:** faz o *smoke test* do executável final e empacota os `.mcpb` com o empacotador fixado em
   `packaging/mcpb/package-lock.json` (`npm ci`).
4. **publish:** também roda no *environment* `release`, só em push de tag. Gera o `SHA256SUMS` e a **atestação de
   proveniência** e cria a release. Ele se recusa a sobrescrever uma release existente, e tags com `-` (ex.:
   `v2.0.0-rc1`) viram *pre-release*.

Configuração única (repositório → Settings):

```bash
GITHUB_REPO=ernaneDomingues/noul-search-agent
# environment `release` exigindo sua aprovação (assinatura e publicação)
gh api -X PUT "repos/$GITHUB_REPO/environments/release" \
  -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)" \
  -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true"
gh api -X POST "repos/$GITHUB_REPO/environments/release/deployment-branch-policies" -f name='v*' -f type=tag
```

- **Proteja as tags `v*`** com o *ruleset* descrito em `docs/deploy/cloud-run.md` (passo 6), para que só
  administradores criem, movam ou apaguem tags de versão.
- **Ative "Release immutability"** (Settings → General → Releases), para que os arquivos de uma release publicada
  não possam ser trocados depois.

## Verificar um arquivo baixado

- **`SHA256SUMS`** só detecta download corrompido: ele está na mesma release que os arquivos.
- **Atestação:** para confirmar que o arquivo foi gerado por este repositório, por este workflow, use:

  ```bash
  gh attestation verify noul-windows-x64.exe --repo ernaneDomingues/noul-search-agent
  ```

## Ativar a assinatura

Crie os segredos **no environment `release`** (Settings → Environments → release → Environment secrets), nunca como
segredos do repositório. Depois:

```bash
gh variable set SIGNING_ENABLED --body true
```

### macOS (Apple Developer Program, US$ 99/ano)

| Segredo | Conteúdo |
|---|---|
| `APPLE_SIGNING_IDENTITY` | ex.: `Developer ID Application: Seu Nome (TEAMID)` |
| `APPLE_CERTIFICATE_P12_BASE64` | certificado *Developer ID Application* exportado como .p12, em base64 (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | senha do .p12 |
| `APPLE_ID`, `APPLE_TEAM_ID` | conta e time Apple |
| `APPLE_APP_PASSWORD` | *app-specific password* para a notarização (appleid.apple.com) |

O job assina com `codesign --options runtime` e notariza com `notarytool`:

- o certificado vai para um *keychain* temporário, com senha aleatória e uso restrito ao `codesign`, que é apagado ao
  final mesmo em caso de falha;
- os *entitlements* `disable-library-validation` e `allow-unsigned-executable-memory` são necessários porque o
  executável *onefile* extrai o Python em tempo de execução.

### Windows (Azure Trusted Signing)

| Segredo | Conteúdo |
|---|---|
| `AZURE_TRUSTED_SIGNING_ENDPOINT` | ex.: `https://eus.codesigning.azure.net/` |
| `AZURE_TRUSTED_SIGNING_ACCOUNT`, `AZURE_CERTIFICATE_PROFILE` | conta e perfil de certificado do Trusted Signing |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | app registration com o papel *Trusted Signing Certificate Profile Signer* |

Uma alternativa é um certificado OV/EV tradicional, que exigiria trocar a etapa por `signtool`.

## Extensão `uv` e o lockfile

A variante `noul.mcpb` traz o `uv.lock`, e o `mcp_config` do manifesto usa `uv run --frozen`. Já um aplicativo que
gerencia o `uv` por conta própria roda `uv run --directory <extensão> noul_server.py`, e pode não passar `--frozen`.
Nesse caso, as versões das dependências são resolvidas a partir dos limites do `pyproject.toml`.
