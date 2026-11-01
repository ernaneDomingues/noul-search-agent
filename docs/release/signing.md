# Assinatura de código (opcional)

Os executáveis e extensões do Noul são publicados **sem assinatura** até existirem certificados. Sem assinatura:

- **Windows:** o SmartScreen mostra "O Windows protegeu o computador". Para executar, clique em **Mais informações →
  Executar assim mesmo**.
- **macOS:** o Gatekeeper bloqueia o executável na primeira execução. Para liberar, clique com o botão direito →
  **Abrir** → Abrir, ou vá em Ajustes do Sistema → Privacidade e Segurança → **Abrir Mesmo Assim**. No terminal:
  `xattr -d com.apple.quarantine ./noul-macos-arm64`.
- **Claude Desktop (.mcpb):** a variante universal (`noul.mcpb`, tipo `uv`) não contém binário. Por isso é a
  recomendada no macOS enquanto não houver assinatura.

O workflow `.github/workflows/release.yml` já tem as etapas de assinatura. Elas rodam **só quando os segredos abaixo
existem** no repositório (Settings → Secrets and variables → Actions → Secrets).

## macOS (Apple Developer Program, US$ 99/ano)

| Segredo | Conteúdo |
|---|---|
| `APPLE_SIGNING_IDENTITY` | ex.: `Developer ID Application: Seu Nome (TEAMID)` |
| `APPLE_CERTIFICATE_P12_BASE64` | certificado *Developer ID Application* exportado como .p12, em base64 (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | senha do .p12 |
| `APPLE_ID`, `APPLE_TEAM_ID` | conta e time Apple |
| `APPLE_APP_PASSWORD` | *app-specific password* para a notarização (appleid.apple.com) |

Com esses segredos, o release assina com `codesign --options runtime` e notariza com `notarytool`.

## Windows (Azure Trusted Signing)

| Segredo | Conteúdo |
|---|---|
| `AZURE_TRUSTED_SIGNING_ENDPOINT` | ex.: `https://eus.codesigning.azure.net/` |
| `AZURE_TRUSTED_SIGNING_ACCOUNT`, `AZURE_CERTIFICATE_PROFILE` | conta e perfil de certificado do Trusted Signing |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | app registration com o papel *Trusted Signing Certificate Profile Signer* |

Uma alternativa é um certificado OV/EV tradicional, que exigiria trocar a etapa por `signtool`.
