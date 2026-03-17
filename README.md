# PsiqMentor v5.5 — Avatar Interativo

Simulador de paciente psiquiátrica com **avatar interativo** (vídeo + voz em tempo real) para treinamento de anamnese psiquiátrica.
Produto educacional — Mestrado em Ensino em Saúde, CESUPA.

## Novidades da v5.5

- **Avatar Interativo**: Paciente Helena aparece como avatar com vídeo e voz em tempo real (via HeyGen LiveAvatar)
- **Entrada por microfone**: Aluno pode falar com a paciente via microfone (transcrição automática)
- **Lip-sync em tempo real**: Avatar fala as respostas com sincronização labial
- **Paciente fixa (Helena)**: Versão dedicada à paciente Helena (Agorafobia) para demonstração do avatar
- **Fallback para texto**: Se a conexão com o avatar falhar, o chat por texto continua funcionando
- **Todas as funcionalidades preservadas**: EEM, pesquisa de satisfação, relatório formativo, PDF, painel admin

## Arquitetura

```
                    ┌──────────────────────┐
                    │   Frontend (HTML)     │
                    │   index.html          │
                    │                       │
                    │  ┌────────────────┐   │
                    │  │ LiveKit WebRTC │◄──┼── Avatar vídeo/áudio
                    │  └────────────────┘   │
                    │  ┌────────────────┐   │
                    │  │  Chat / Input  │   │
                    │  └───────┬────────┘   │
                    └──────────┼────────────┘
                               │
                    ┌──────────▼────────────┐
                    │  Backend (FastAPI)     │
                    │  api_server.py         │
                    │                       │
                    │  Claude ←→ /api/chat  │
                    │  LiveAvatar ←→ /api/  │
                    │    liveavatar-token    │
                    │    liveavatar-start    │
                    └───────────────────────┘
```

### Fluxo de Dados

1. Aluno digita texto **OU** fala pelo microfone
2. Se microfone: LiveAvatar transcreve → evento `user.transcription` → frontend captura texto
3. Texto enviado ao `/api/chat` → Claude gera resposta da paciente
4. Frontend exibe resposta completa no chat (com descrições comportamentais `*...*`)
5. Frontend envia texto limpo (sem `*...*`) ao avatar via `avatar.speak_text`
6. Avatar fala a resposta com lip-sync

### LiveAvatar FULL Mode

Usa LiveAvatar em **modo FULL** sem `context_id`:
- LiveAvatar cuida de: VAD, STT, WebRTC, streaming de vídeo
- LiveAvatar **não** gera respostas LLM (sem context = sem LLM)
- Claude (nosso backend) permanece como o "cérebro" da paciente
- Usamos `avatar.speak_text` para fazer o avatar falar

## Estrutura

```
psiq-mentor-v5.5/
├── api_server.py          # Backend FastAPI + endpoints LiveAvatar
├── index.html             # Frontend completo (single-page app)
├── requirements.txt       # Dependências Python
└── README.md
```

## Variáveis de Ambiente

| Variável | Valor | Descrição |
|----------|-------|----------|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Chave da API Anthropic (Claude) |
| `LIVEAVATAR_API_KEY` | `f6433a1d-218c-11f1-a99e-066a7fa2e369` | Chave da API LiveAvatar (HeyGen) |
| `LIVEAVATAR_AVATAR_ID` | `bf00036b-558a-44b5-b2ff-1e3cec0f4ceb` | Avatar "Marianne Sitting" |
| `LIVEAVATAR_VOICE_ID` | `9318da2e-4058-490f-8d19-9141bfb60b66` | Voz "Helena PT-BR" (ElevenLabs via LiveAvatar) |
| `ADMIN_USER` | `admin` | Usuário do painel admin |
| `ADMIN_PASS` | `Mestrado2026` | Senha do painel admin |

### Voz em Português

A voz do avatar usa **ElevenLabs** (modelo Multilingual v2) vinculada ao LiveAvatar como third-party voice.
Isso permite que a Marianne Sitting fale em português brasileiro com lip-sync.
A key da ElevenLabs já está registrada como secret no LiveAvatar (secret_id: `9f480e7d-57fb-41d0-992a-93082834d065`).

## Instalação Local

```bash
# Instalar dependências
pip install -r requirements.txt

# Definir variáveis de ambiente
export ANTHROPIC_API_KEY="sk-ant-..."
export LIVEAVATAR_API_KEY="f6433a1d-218c-11f1-a99e-066a7fa2e369"
export LIVEAVATAR_AVATAR_ID="bf00036b-558a-44b5-b2ff-1e3cec0f4ceb"
export LIVEAVATAR_VOICE_ID="9318da2e-4058-490f-8d19-9141bfb60b66"

# Iniciar o servidor
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

Acesse: `http://localhost:8000`

## Deploy

### Render
1. Crie um Web Service no [render.com](https://render.com/)
2. Conecte o repositório GitHub
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn api_server:app --host 0.0.0.0 --port $PORT`
5. Adicione as variáveis de ambiente no painel "Environment"

## Modelos de IA Utilizados

- **Claude Sonnet 4** — Simulação da paciente e avaliação de qualidade
- **Claude Haiku 4** — Rastreamento silencioso de critérios DSM-5-TR (mais econômico)
- **HeyGen LiveAvatar** — Avatar com vídeo em tempo real (Marianne Sitting)
- **ElevenLabs Multilingual v2** — Voz feminina em português brasileiro (via LiveAvatar third-party voice)

## Endpoints da API

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | `/api/start?patient=Helena` | Inicia sessão com Helena |
| POST | `/api/chat` | Envia mensagem e recebe resposta |
| POST | `/api/liveavatar-token` | Obtém token de sessão LiveAvatar |
| POST | `/api/liveavatar-start` | Inicia sessão LiveAvatar |
| POST | `/api/eem-summary` | Gera resumo observacional para EEM |
| POST | `/api/eem-submit` | Submete avaliação EEM do aluno |
| POST | `/api/finish` | Encerra sessão e gera relatório |
| POST | `/api/survey` | Submete pesquisa de satisfação |
| GET | `/api/health` | Status do sistema |
| POST | `/api/admin/login` | Login do painel admin |
| GET | `/api/admin/dashboard` | Dashboard administrativo |

## Custo Estimado

- **Claude**: ~US$ 0.05–0.10 por simulação completa (15-20 turnos)
- **LiveAvatar**: US$ 0.20/minuto de sessão (vídeo do avatar)
- **ElevenLabs**: Starter US$ 5/mês (30.000 caracteres) — suficiente para ~100+ simulações de teste
