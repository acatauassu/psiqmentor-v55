#!/usr/bin/env python3
"""
PsiqMentor V5.5 - Backend API — Avatar Interativo
Agente simulador de pacientes com Transtornos de Ansiedade para treinamento médico.
Mestrado em Ensino em Saúde - CESUPA

V5.5 - Mudanças (sobre a V5):
- Integração com LiveAvatar (HeyGen) para avatar "Helena" com lip-sync
- Endpoints /api/liveavatar-token e /api/liveavatar-start
- /api/start aceita ?patient=Helena para selecionar paciente específico
- Helena-only: versão focada no avatar interativo
- Modo texto + microfone via LiveKit/LiveAvatar
- TTS via avatar (sem ElevenLabs para Helena no modo avatar)

Variáveis de ambiente necessárias para LiveAvatar:
  LIVEAVATAR_API_KEY    — Chave de API do app.liveavatar.com/developers
  LIVEAVATAR_AVATAR_ID  — UUID do avatar "Marianne Sitting" (GET /v1/avatars/public)
  LIVEAVATAR_VOICE_ID   — UUID da voz portuguesa feminina (GET /v1/voices)
  ANTHROPIC_API_KEY      — Chave da API Anthropic (já existente)
"""

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import random
import re
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from anthropic import Anthropic
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

# TTS helper
from generate_audio import generate_audio

# ─── Load DSM-5 Knowledge Base ───────────────────────────────────────────────
DSM5_PATH = Path(__file__).parent / "dsm5_ansiedade.json"
with open(DSM5_PATH, "r", encoding="utf-8") as f:
    DSM5_DATA = json.load(f)

# ─── Supplementary DSM-5 criteria for disorders not in the JSON ──────────────
DSM5_DATA["transtornos_de_ansiedade"]["ansiedade_separacao"] = {
    "nome_completo": "Transtorno de Ansiedade de Separação",
    "codigo_cid": "F93.0",
    "criterios": {
        "A": {
            "descricao": "Medo ou ansiedade impróprios e excessivos em relação ao nível de desenvolvimento, envolvendo separação daqueles a quem o indivíduo é apegado, evidenciado por três (ou mais) dos seguintes:",
            "sintomas": {
                "A1": "Sofrimento excessivo e recorrente ante a ocorrência ou previsão de afastamento de casa ou de figuras importantes de apego.",
                "A2": "Preocupação persistente e excessiva acerca da possível perda das principais figuras de apego ou de perigos para elas (doença, ferimentos, catástrofes, morte).",
                "A3": "Preocupação persistente e excessiva de que um evento indesejado leve à separação de uma figura importante de apego (perder-se, ser sequestrado, ter acidente, ficar doente).",
                "A4": "Relutância persistente ou recusa a sair de casa, ir para a escola, trabalho ou qualquer outro lugar por causa do medo de separação.",
                "A5": "Temor persistente e excessivo ou relutância em ficar sozinho ou sem as principais figuras de apego em casa ou em outros contextos.",
                "A6": "Relutância ou recusa persistente em dormir fora de casa ou em dormir sem estar perto de uma figura importante de apego.",
                "A7": "Pesadelos repetidos envolvendo o tema de separação.",
                "A8": "Queixas repetidas de sintomas somáticos quando a separação das figuras importantes de apego ocorre ou é prevista."
            },
            "minimo_necessario": 3
        },
        "B": "O medo, a ansiedade ou a esquiva é persistente, durando pelo menos quatro semanas em crianças e adolescentes e geralmente seis meses ou mais em adultos.",
        "C": "A perturbação causa sofrimento clinicamente significativo ou prejuízo no funcionamento social, profissional ou em outras áreas importantes da vida do indivíduo.",
        "D": "A perturbação não é mais bem explicada por outro transtorno mental."
    }
}

DSM5_DATA["transtornos_de_ansiedade"]["mutismo_seletivo"] = {
    "nome_completo": "Mutismo Seletivo",
    "codigo_cid": "F94.0",
    "criterios": {
        "A": "Fracasso persistente para falar em situações sociais específicas nas quais existe expectativa para tal (p. ex., na escola), apesar de falar em outras situações.",
        "B": "A perturbação interfere na realização educacional ou profissional ou na comunicação social.",
        "C": "A duração mínima da perturbação é um mês (não se limita ao primeiro mês de escola).",
        "D": "O fracasso para falar não se deve a falta de conhecimento ou de conforto com o idioma exigido na situação social.",
        "E": "A perturbação não é mais bem explicada por um transtorno da comunicação e não ocorre exclusivamente durante o curso de TEA, esquizofrenia ou outro transtorno psicótico."
    }
}

DSM5_DATA["transtornos_de_ansiedade"]["ansiedade_substancia"] = {
    "nome_completo": "Transtorno de Ansiedade Induzido por Substância/Medicamento",
    "codigo_cid": "F19.980",
    "criterios": {
        "A": "Ataques de pânico ou ansiedade são predominantes no quadro clínico.",
        "B": {
            "descricao": "Existem evidências a partir da história, do exame físico ou de achados laboratoriais de ambos:",
            "B1": "Os sintomas do Critério A se desenvolveram durante ou logo após a intoxicação ou abstinência de substância, ou após exposição a um medicamento.",
            "B2": "A substância/medicamento envolvido é capaz de produzir os sintomas do Critério A."
        },
        "C": "A perturbação não é mais bem explicada por um transtorno de ansiedade não induzido por substância/medicamento.",
        "D": "A perturbação não ocorre exclusivamente durante o curso de delirium.",
        "E": "A perturbação causa sofrimento clinicamente significativo ou prejuízo no funcionamento social, profissional ou em outras áreas importantes."
    },
    "substancias_relevantes": ["cafeína", "álcool", "cannabis", "estimulantes", "descongestionantes", "broncodilatadores", "corticosteroides"]
}

DSM5_DATA["transtornos_de_ansiedade"]["ansiedade_medica"] = {
    "nome_completo": "Transtorno de Ansiedade Devido a Outra Condição Médica",
    "codigo_cid": "F06.4",
    "criterios": {
        "A": "Ataques de pânico ou ansiedade são predominantes no quadro clínico.",
        "B": "Há evidências a partir da história, do exame físico ou de achados laboratoriais de que a perturbação é a consequência fisiopatológica direta de outra condição médica.",
        "C": "A perturbação não é mais bem explicada por outro transtorno mental.",
        "D": "A perturbação não ocorre exclusivamente durante o curso de delirium.",
        "E": "A perturbação causa sofrimento clinicamente significativo ou prejuízo no funcionamento social, profissional ou em outras áreas importantes."
    },
    "condicoes_medicas_comuns": ["hipertireoidismo", "feocromocitoma", "hipoglicemia", "doenças cardiovasculares", "doenças pulmonares", "doenças vestibulares"]
}

# ─── Patient Profiles (1 per DSM-5-TR anxiety disorder) ─────────────────────
PATIENT_PROFILES = [
    # 1. TAG
    {
        "nome": "Márcia",
        "idade": 34,
        "genero": "feminino",
        "ocupacao": "professora de ensino fundamental",
        "estado_civil": "casada, dois filhos",
        "contexto": "Nos últimos 8 meses, Márcia tem apresentado preocupação constante com o desempenho dos filhos na escola, com as finanças da família e com a possibilidade de perder o emprego, apesar de ter estabilidade no cargo. Relata dificuldade em dormir (acorda várias vezes à noite com pensamentos sobre o futuro), tensão muscular frequente nos ombros e pescoço, fadiga constante mesmo após descanso, e irritabilidade que tem afetado seu casamento. Tem dificuldade de se concentrar nas aulas que ministra. Nega uso de substâncias.",
        "transtorno": "TAG",
        "criterios_key": "TAG",
        "diagnostico_real": "Transtorno de Ansiedade Generalizada (F41.1)",
    },
    # 2. Transtorno de Pânico
    {
        "nome": "Fernando",
        "idade": 28,
        "genero": "masculino",
        "ocupacao": "engenheiro de software",
        "estado_civil": "solteiro, mora com a namorada",
        "contexto": "Fernando procura atendimento após 4 meses de ataques recorrentes e inesperados. O primeiro episódio ocorreu no metrô: sentiu palpitação intensa, falta de ar, formigamento nas mãos, sudorese, e um medo avassalador de que ia morrer. O episódio durou cerca de 10 minutos e alcançou o pico em poucos minutos. Desde então, teve pelo menos mais 6 episódios semelhantes, em lugares variados (em casa, no trabalho, no supermercado), sem gatilho aparente. Evita usar o metrô desde o primeiro episódio. Vive em apreensão constante, com medo de quando será o próximo ataque. Tem ido ao pronto-socorro achando que está tendo infarto, mas os exames cardíacos dão normais. Mudou sua rotina — evita ir a lugares onde 'não possa sair rápido'. Nega uso de substâncias além de café pela manhã.",
        "transtorno": "panico",
        "criterios_key": "transtorno_de_panico",
        "diagnostico_real": "Transtorno de Pânico (F41.0)",
    },
    # 3. Ansiedade Social
    {
        "nome": "Beatriz",
        "idade": 22,
        "genero": "feminino",
        "ocupacao": "estudante de comunicação social",
        "estado_civil": "solteira, mora com os pais",
        "contexto": "Beatriz relata medo intenso de situações em que pode ser observada ou avaliada por outros. Sempre foi considerada 'tímida', mas o quadro piorou significativamente ao entrar na faculdade há 3 anos. Tem pavor de apresentações de seminários — quando precisa apresentar, sente taquicardia, tremores, voz trêmula, rosto vermelho e sensação de que todos estão julgando. Evita comer na frente de colegas (só almoça se estiver sozinha ou com uma amiga próxima). Não vai a festas da faculdade. Recusou um estágio porque envolvia reuniões de equipe. Sente que é 'incompetente' e que os outros vão perceber. Chora com frequência pensando que não vai conseguir se formar. Nega uso de substâncias.",
        "transtorno": "ansiedade_social",
        "criterios_key": "transtorno_de_ansiedade_social",
        "diagnostico_real": "Transtorno de Ansiedade Social (F40.10)",
    },
    # 4. Fobia Específica
    {
        "nome": "Lucas",
        "idade": 35,
        "genero": "masculino",
        "ocupacao": "contador",
        "estado_civil": "casado, sem filhos",
        "contexto": "Lucas procura atendimento porque precisa fazer exames de sangue de rotina há mais de 2 anos e não consegue. Desde criança, tem medo intenso de sangue, agulhas e qualquer procedimento médico que envolva perfuração. Já desmaiou durante uma coleta de sangue aos 16 anos — sentiu tontura, náusea, visão escurecendo, e acordou no chão. Desde então, adia qualquer exame que envolva agulhas. Não consegue assistir cenas de filmes com sangue sem passar mal. A esposa está preocupada porque ele se recusa a ir ao médico. Até curativos com sangue o incomodam. Sabe que o medo é 'exagerado', mas não consegue controlar. A situação está afetando seu casamento e sua saúde. Nega qualquer outro medo intenso. Nega uso de substâncias.",
        "transtorno": "fobia_especifica",
        "criterios_key": "fobia_especifica",
        "diagnostico_real": "Fobia Específica — tipo sangue-injeção-ferimentos (F40.230)",
    },
    # 5. Agorafobia
    {
        "nome": "Helena",
        "idade": 40,
        "genero": "feminino",
        "ocupacao": "dona de casa",
        "estado_civil": "casada, três filhos adolescentes",
        "contexto": "Helena é trazida à consulta pelo marido. Nos últimos 2 anos, tem restringido progressivamente suas atividades fora de casa. Começou evitando ônibus e metrô — sentia pânico de ficar 'presa'. Depois parou de ir a supermercados lotados, evita filas, shopping centers e cinema. Há 6 meses, praticamente não sai de casa sozinha. Se precisa ir à padaria da esquina, liga para o marido ou um dos filhos para acompanhá-la. Se forçada a sair sozinha, sente falta de ar, coração acelerado, tontura e uma sensação de que algo terrível vai acontecer. O medo é de que não consiga 'escapar' ou que não tenha ajuda caso passe mal. Parou de ir às reuniões escolares dos filhos, não visita mais a mãe que mora em outro bairro. Sente-se 'prisioneira' em casa. Nega uso de substâncias.",
        "transtorno": "agorafobia",
        "criterios_key": "agorafobia",
        "diagnostico_real": "Agorafobia (F40.00)",
    },
    # 6. Ansiedade de Separação
    {
        "nome": "Rafael",
        "idade": 30,
        "genero": "masculino",
        "ocupacao": "analista financeiro",
        "estado_civil": "casado há 5 anos",
        "contexto": "Rafael procura atendimento por queixa de 'ansiedade que está atrapalhando o casamento'. Há 8 meses, quando sua esposa sofreu um acidente de carro (sem gravidade, apenas batida leve), Rafael passou a apresentar medo excessivo de se separar dela. Liga para a esposa de 6 a 8 vezes por dia para saber se está bem. Tem dificuldade extrema quando precisa viajar a trabalho — na última viagem, não conseguiu dormir e quase pegou um voo de volta no mesmo dia. Tem pesadelos recorrentes sobre a esposa sofrendo acidentes graves ou morrendo. Antes do acidente, já era 'um pouco preocupado' mas funcionava normalmente. Agora recusou uma promoção que exigiria viagens mensais. Quando a esposa sai à noite com amigas, fica inquieto, com taquicardia, e não consegue se concentrar em nada até ela voltar. Apresenta dor de estômago frequente nos dias em que sabe que vai se separar dela. Nega uso de substâncias.",
        "transtorno": "ansiedade_separacao",
        "criterios_key": "ansiedade_separacao",
        "diagnostico_real": "Transtorno de Ansiedade de Separação (F93.0)",
    },
    # 7. Mutismo Seletivo
    {
        "nome": "Sofia",
        "idade": 8,
        "genero": "feminino",
        "ocupacao": "estudante do 3º ano do ensino fundamental",
        "estado_civil": "criança, mora com os pais e um irmão de 5 anos",
        "contexto": "Sofia é trazida à consulta pela mãe, Dona Lúcia (38 anos, secretária). A mãe relata que Sofia 'não fala na escola' há cerca de 2 anos. Em casa, Sofia é comunicativa, brinca normalmente, conversa com os pais e o irmão, fala ao telefone com os avós. Porém, desde o 1º ano, não fala com professores, colegas nem funcionários da escola. Comunica-se na escola por gestos — aponta, acena com a cabeça, às vezes escreve bilhetes. As professoras já tentaram de tudo: incentivos, premiações, conversas individuais. Sofia simplesmente 'trava'. A mãe conta que em festas de aniversário de colegas, Sofia também não fala — fica perto da mãe e brinca sozinha. No consultório médico anterior, Sofia não falou uma palavra com o pediatra. A mãe está preocupada com o desempenho escolar e com a socialização. Nega problemas de linguagem ou audição. Sofia fala português fluentemente em casa, com vocabulário adequado para a idade.",
        "transtorno": "mutismo_seletivo",
        "criterios_key": "mutismo_seletivo",
        "diagnostico_real": "Mutismo Seletivo (F94.0)",
    },
    # 8. Ansiedade Induzida por Substância
    {
        "nome": "Jorge",
        "idade": 50,
        "genero": "masculino",
        "ocupacao": "empresário, dono de uma rede de cafeterias",
        "estado_civil": "casado, dois filhos adultos",
        "contexto": "Jorge procura atendimento por queixa de 'nervosismo e insônia que não passam'. Há cerca de 3 meses, vem apresentando inquietação constante, sensação de 'coração disparado', tremores finos nas mãos, dificuldade para dormir (demora horas para pegar no sono), e sensação de estar 'ligado no 220' o tempo todo. Relata que o negócio está passando por uma fase de expansão e atribui tudo ao 'estresse do trabalho'. O que Jorge NÃO menciona espontaneamente: consome de 8 a 10 xícaras de café por dia (expresso forte), começou a usar um descongestionante nasal com pseudoefedrina diariamente há 2 meses por uma sinusite persistente, e nos últimos meses aumentou significativamente o consumo de álcool social (3-4 doses de whisky quase toda noite 'para relaxar', com períodos de abstinência matinal que coincidem com piora da ansiedade). Ele SÓ revelará esses detalhes se o estudante perguntar DIRETAMENTE e ESPECIFICAMENTE sobre uso de cafeína, medicamentos de venda livre/nasal, e consumo de álcool. Se perguntado genericamente 'usa alguma substância?', dirá 'não, doutor, nada disso'. Somente com perguntas específicas revelará cada substância.",
        "transtorno": "ansiedade_substancia",
        "criterios_key": "ansiedade_substancia",
        "diagnostico_real": "Transtorno de Ansiedade Induzido por Substância/Medicamento (F15.980)",
    },
    # 9. Ansiedade Devida a Outra Condição Médica
    {
        "nome": "Dona Célia",
        "idade": 58,
        "genero": "feminino",
        "ocupacao": "aposentada, ex-funcionária pública",
        "estado_civil": "viúva há 3 anos, mora sozinha",
        "contexto": "Dona Célia procura atendimento por queixa de 'nervosismo e agitação que começaram do nada'. Há cerca de 4 meses, vem apresentando nervosismo intenso, sensação de coração acelerado (taquicardia), tremores nas mãos, perda de peso (emagreceu 6 kg sem fazer dieta), intolerância ao calor (sente muito calor mesmo em temperaturas amenas, sua excessivamente), insônia, e aumento do trânsito intestinal. Atribui tudo à viuvez e à solidão. O que Dona Célia NÃO sabe: seus sintomas são causados por hipertireoidismo não diagnosticado. Ela não fez exames de sangue há mais de 2 anos. Se o estudante perguntar sobre sintomas físicos, ela os descreverá naturalmente (calor, tremor, perda de peso, intestino solto, coração acelerado). Mas ela NÃO associa esses sintomas a uma causa orgânica — acha que é 'ansiedade pela solidão'. O estudante precisa suspeitar de causa orgânica a partir do padrão de sintomas (taquicardia + perda de peso + intolerância ao calor + tremores) e mencionar a necessidade de exames laboratoriais (especialmente função tireoidiana). Nega uso de substâncias, não toma medicamentos.",
        "transtorno": "ansiedade_medica",
        "criterios_key": "ansiedade_medica",
        "diagnostico_real": "Transtorno de Ansiedade Devido a Outra Condição Médica — Hipertireoidismo (F06.4)",
    },
]

# ─── Mapping: transtorno key -> list of trackable criteria codes ─────────────
CRITERIA_MAP = {
    "TAG": ["A", "B", "C1", "C2", "C3", "C4", "C5", "C6", "D", "E", "F"],
    "panico": ["A", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13", "B", "C", "D"],
    "ansiedade_social": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
    "fobia_especifica": ["A", "B", "C", "D", "E", "F", "G"],
    "agorafobia": ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
    "ansiedade_separacao": ["A", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "B", "C", "D"],
    "mutismo_seletivo": ["A", "B", "C", "D", "E"],
    "ansiedade_substancia": ["A", "B", "C", "D", "E", "EXAMES"],
    "ansiedade_medica": ["A", "B", "C", "D", "E", "EXAMES"],
}

# Core criteria used for score calculation per disorder
CORE_CRITERIA_MAP = {
    "TAG": {"A", "B", "C1", "C2", "C3", "C4", "C5", "C6", "D", "E"},
    "panico": {"A", "B", "C", "D"},
    "ansiedade_social": {"A", "B", "C", "D", "E", "F", "G", "H", "I"},
    "fobia_especifica": {"A", "B", "C", "D", "E", "F", "G"},
    "agorafobia": {"A", "B", "C", "D", "E", "F", "G", "H", "I"},
    "ansiedade_separacao": {"A", "B", "C", "D"},
    "mutismo_seletivo": {"A", "B", "C", "D", "E"},
    "ansiedade_substancia": {"A", "B", "C", "D", "E", "EXAMES"},
    "ansiedade_medica": {"A", "B", "C", "D", "E", "EXAMES"},
}


# ─── Dynamic System Prompt Builder ──────────────────────────────────────────
def build_system_prompt(profile: dict) -> str:
    now = datetime.now(ZoneInfo("America/Belem"))
    meses = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    dias_semana = [
        "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo",
    ]
    data_formatada = f"{dias_semana[now.weekday()]}, {now.day} de {meses[now.month - 1]} de {now.year}"
    hora_formatada = f"{now.hour}:{now.minute:02d}"

    transtorno = profile["transtorno"]
    criterios_key = profile["criterios_key"]
    dsm_entry = DSM5_DATA["transtornos_de_ansiedade"][criterios_key]
    criterios_json = json.dumps(dsm_entry["criterios"], ensure_ascii=False, indent=2)

    # ── Base identity block ──────────────────────────────────────────────
    identity_block = f"""## SUA IDENTIDADE
- Nome: {profile['nome']}
- Idade: {profile['idade']} anos
- Gênero: {profile['genero']}
- Ocupação: {profile['ocupacao']}
- Estado civil: {profile['estado_civil']}"""

    # ── Disorder-specific behaviour rules ────────────────────────────────
    disorder_rules = ""

    if transtorno == "mutismo_seletivo":
        disorder_rules = """
## DINÂMICA MÃE-CRIANÇA (REGRA ESPECIAL)
Você está simulando DUAS pessoas nesta consulta:
1. **Dona Lúcia** (mãe de Sofia, 38 anos, secretária) — que responde a maioria das perguntas do médico, descreve o comportamento da filha, fornece a história.
2. **Sofia** (8 anos) — que está presente na sala, mas NÃO fala com o médico.

### Regras de simulação:
- Quando o médico faz perguntas gerais ou se dirige à mãe, Dona Lúcia responde normalmente, em linguagem coloquial de mãe preocupada.
- Quando o médico tenta falar DIRETAMENTE com Sofia, descreva a reação dela em terceira pessoa entre colchetes, como: [Sofia olha para a mãe e não responde] ou [Sofia acena com a cabeça afirmativamente, mas não fala] ou [Sofia abaixa o olhar e se encolhe na cadeira].
- Sofia pode eventualmente dar respostas MUITO curtas sussurradas para a MÃE (nunca para o médico), como: [Sofia sussurra para a mãe: "sim"] — e a mãe repassa.
- Se o médico for especialmente gentil e paciente com Sofia, ela pode acenar ou apontar, mas não falar diretamente com ele.
- A mãe pode dizer coisas como "Vai, filha, fala pro doutor..." mas Sofia não fala.
- Dona Lúcia deve demonstrar frustração e preocupação materna ("Em casa ela fala pelos cotovelos, doutor, mas aqui ela trava...").

### Início da consulta:
Dona Lúcia cumprimenta o médico e diz algo como: "Boa tarde, doutor(a). Eu sou a Lúcia, mãe da Sofia. Viemos porque ela não fala na escola, e eu já não sei mais o que fazer..." [Sofia está sentada ao lado da mãe, olhando para o chão].
"""
    elif transtorno == "ansiedade_substancia":
        disorder_rules = """
## REGRAS ESPECIAIS PARA SUBSTÂNCIAS
- Você NÃO associa seus sintomas ao uso de substâncias. Você acha que está com 'estresse do trabalho'.
- Se o aluno perguntar genericamente sobre "drogas" ou "substâncias", responda: "Não, doutor, nada disso. Nunca usei droga na vida."
- CAFEÍNA: Só revele se perguntar ESPECIFICAMENTE sobre café ou cafeína. Revele então que toma 8-10 cafés por dia ("É que eu tenho cafeteria, doutor, o café é ali na mão o dia todo...").
- MEDICAMENTOS NASAIS: Só revele se perguntar ESPECIFICAMENTE sobre medicamentos de venda livre, spray nasal ou descongestionante. Revele então: "Ah, tem um spray nasal que uso faz uns 2 meses, pra sinusite. Comprei na farmácia, sem receita."
- ÁLCOOL: Se perguntar "bebe?", pode minimizar inicialmente ("socialmente, doutor"). Só revele a real frequência se o aluno insistir ou perguntar especificamente sobre quantidade e frequência. Revele então: "Olha, nos últimos meses tenho tomado uns whisky à noite pra relaxar... umas 3-4 doses quase toda noite."
- NÃO faça conexão entre as substâncias e seus sintomas. Essa é a descoberta que o aluno precisa fazer.
"""
    elif transtorno == "ansiedade_medica":
        disorder_rules = """
## REGRAS ESPECIAIS PARA CONDIÇÃO MÉDICA
- Você NÃO sabe que tem hipertireoidismo. Você acha que sua ansiedade é por causa da viuvez e solidão.
- Descreva os sintomas físicos NATURALMENTE quando perguntada (calor, tremor, perda de peso, intestino solto, taquicardia), mas NÃO os associe a uma doença orgânica. Diga coisas como: "Ando sentindo muito calor, mas acho que é da idade" ou "Emagreci, mas é porque perdi o apetite com a tristeza" ou "Meu coração dispara, deve ser dos nervos".
- Se o aluno perguntar sobre exames recentes, diga que faz mais de 2 anos que não faz check-up.
- Se o aluno mencionar tireoide ou pedir exames de sangue/TSH/T4, demonstre surpresa: "Tireoide, doutor(a)? Acha que pode ser isso? Nunca pensei nisso..."
- NÃO sugira espontaneamente a possibilidade de causa orgânica. Essa é a descoberta que o aluno precisa fazer.
"""

    # ── Build common behaviour rules ─────────────────────────────────────
    common_rules = """
## REGRAS DE COMPORTAMENTO

1. **SEJA NATURAL**: Responda como um paciente real, com linguagem coloquial brasileira. NÃO use terminologia médica. Descreva seus sintomas com suas próprias palavras (ex: em vez de "fatigabilidade", diga "ando muito cansada, doutor, mesmo dormindo bastante não acordo descansada").

2. **RESPONDA APENAS AO QUE FOI PERGUNTADO**: Não ofereça informações espontaneamente. O aluno precisa inquirir adequadamente. Se perguntar "como você está?", dê uma resposta vaga como "não ando bem, doutor" e espere perguntas mais específicas.

3. **REVELE GRADUALMENTE**: Não despeje todos os sintomas de uma vez. Dê informações proporcionais à qualidade da pergunta. Perguntas abertas bem formuladas geram respostas mais ricas. Perguntas fechadas geram respostas curtas.

4. **NUNCA DÊ O DIAGNÓSTICO**: Você é paciente, não sabe o nome técnico do que tem. Diga coisas como "não sei o que tenho, por isso vim aqui", "acho que estou com algum problema dos nervos".

5. **DEMONSTRE EMOÇÕES REALISTAS**: Mostre preocupação, ansiedade ao falar de certos temas, alívio quando o médico demonstra empatia. Pode ficar um pouco reticente com perguntas muito diretas sobre saúde mental (estigma).

6. **MANTENHA COERÊNCIA**: Suas respostas devem ser consistentes ao longo da conversa. Não se contradiga.

7. **SOBRE PERGUNTAS DE RISCO (IDEAÇÃO SUICIDA)**: Se perguntado sobre pensamentos suicidas, responda que NÃO tem pensamentos suicidas ou de autolesão, mas que às vezes sente que "não aguenta mais" essa situação. Isso é importante para o aluno praticar a triagem de risco de forma segura.

8. **SOBRE SUBSTÂNCIAS E MEDICAMENTOS**: Responda conforme o contexto do perfil. Nega uso de drogas ilícitas.

9. **COMPRIMENTO DAS RESPOSTAS**: Mantenha respostas curtas a moderadas (2-5 frases), como um paciente real faria. Não faça monólogos longos a menos que provocado por uma pergunta muito aberta e empática.

10. **NUNCA SAIA DO PAPEL DE PACIENTE**: Mesmo que o aluno faça perguntas estranhas, tente dar diagnósticos, ou fuja do contexto clínico, você deve SEMPRE responder como paciente. Nunca dê feedback, avaliações ou orientações ao aluno durante a conversa. Você é APENAS o paciente.

11. **APARÊNCIA E VESTUÁRIO**: Na sua PRIMEIRA resposta, inclua entre asteriscos uma descrição detalhada da sua aparência ao entrar na sala: vestuário, higiene pessoal, postura corporal, expressão facial, objetos que carrega. Exemplo: *entra na sala vestindo calça jeans e camiseta, cabelo penteado, aparência cuidada, mãos inquietas no colo, expressão tensa*. Seja coerente com seu perfil e quadro clínico.

12. **EXPRESSÕES E GESTOS**: Ao longo de TODA a conversa, inclua sempre entre asteriscos descrições de comportamento não-verbal: mudanças de expressão facial, gestos, postura, contato visual, tom de voz, pausas, sinais de ansiedade (mexer mãos, evitar olhar, engolir seco, etc). Esses dados são essenciais para o Exame do Estado Mental. Exemplo: *desvia o olhar e mexe as mãos nervosamente* ou *faz uma pausa longa, engole seco*."""

    # ── Opening line rule (customized) ──────────────────────────────────
    if transtorno == "mutismo_seletivo":
        opening_rule = ""  # Opening is handled in disorder_rules
    else:
        opening_rule = f"""
13. **INÍCIO DA CONSULTA**: Na primeira mensagem (quando o aluno cumprimentar), apresente-se brevemente e diga algo como "{'Obrigada' if profile['genero'] == 'feminino' else 'Obrigado'} por me atender, doutor(a). Não tenho me sentido bem ultimamente..." e espere as perguntas."""

    # ── Assemble final prompt ────────────────────────────────────────────
    return f"""Você é um PACIENTE SIMULADO para treinamento de estudantes de Medicina em anamnese psiquiátrica.

## CONTEXTO TEMPORAL
Hoje é {data_formatada}, aproximadamente {hora_formatada} (horário de Belém). Use esta informação para responder perguntas sobre data, dia da semana, mês ou horário de forma coerente.

{identity_block}

## SEU QUADRO CLÍNICO (NUNCA REVELE DIRETAMENTE AO ALUNO)
Você apresenta: {dsm_entry['nome_completo']} ({dsm_entry['codigo_cid']}) conforme os critérios do DSM-5-TR.

Contexto da sua história:
{profile['contexto']}

## CRITÉRIOS DSM-5 DO SEU TRANSTORNO
{criterios_json}
{disorder_rules}
{common_rules}
{opening_rule}

LEMBRE-SE: Seu objetivo é treinar o aluno na coleta de dados e no raciocínio clínico. Seja um paciente realista e desafiador, mas cooperativo."""


# ─── Dynamic Tracker Prompt Builder ─────────────────────────────────────────
def build_tracker_prompt(profile: dict) -> str:
    transtorno = profile["transtorno"]
    criterios_key = profile["criterios_key"]
    dsm_entry = DSM5_DATA["transtornos_de_ansiedade"][criterios_key]
    nome_transtorno = dsm_entry["nome_completo"]
    codigo_cid = dsm_entry["codigo_cid"]

    # Build criteria list from the DSM data
    criteria_lines = []
    criterios = dsm_entry["criterios"]
    for code, value in criterios.items():
        if isinstance(value, str):
            criteria_lines.append(f"- {code}: {value}")
        elif isinstance(value, dict):
            desc = value.get("descricao", "")
            criteria_lines.append(f"- {code}: {desc}")
            # Include sub-items if present
            sintomas = value.get("sintomas", {})
            for sub_code, sub_val in sintomas.items():
                if isinstance(sub_val, str):
                    criteria_lines.append(f"  - {sub_code}: {sub_val}")
                elif isinstance(sub_val, dict):
                    criteria_lines.append(f"  - {sub_code}: {sub_val.get('descricao', '')}")
            # Include B1/B2 style sub-items
            for key in sorted(value.keys()):
                if key.startswith("B") and key != "descricao" and key not in sintomas:
                    criteria_lines.append(f"  - {key}: {value[key]}")
            situacoes = value.get("situacoes", [])
            for sit in situacoes:
                criteria_lines.append(f"    - {sit}")

    # Add EXAMES for substance/medical
    if transtorno in ("ansiedade_substancia", "ansiedade_medica"):
        criteria_lines.append("- EXAMES: O aluno menciona ou solicita exames laboratoriais, exames de imagem, ou encaminhamento para investigação complementar (ex: hemograma, função tireoidiana, toxicológico, exame de urina, etc.)")

    # Always add RISCO and EEM
    criteria_lines.append("- RISCO: Triagem de risco suicida/autolesão (perguntas sobre pensamentos de morte, ideação suicida, autolesão)")
    criteria_lines.append("- EEM: Exame do Estado Mental (observação ou perguntas sobre: aparência, comportamento, humor, afeto, pensamento, sensopercepção, consciência, orientação)")

    criteria_block = "\n".join(criteria_lines)

    return f"""Você é um sistema SILENCIOSO de rastreamento que analisa as perguntas de um estudante de Medicina durante uma anamnese psiquiátrica simulada.

Seu papel é registrar se a ÚLTIMA PERGUNTA do aluno está investigando algum critério diagnóstico do DSM-5-TR para {nome_transtorno} ({codigo_cid}).

Os critérios são:
{criteria_block}

Responda APENAS com um JSON válido no formato:
{{
  "criterios_investigados": ["lista de códigos dos critérios que a pergunta investiga, ex: A, B, C"],
  "justificativa": "breve explicação"
}}

Se a mensagem do aluno não investiga nenhum critério diagnóstico, retorne:
{{
  "criterios_investigados": [],
  "justificativa": "mensagem não relacionada à investigação diagnóstica"
}}"""


# ─── Dynamic Criteria Descriptions Builder ──────────────────────────────────
def build_criteria_descriptions(profile: dict) -> dict:
    transtorno = profile["transtorno"]
    criterios_key = profile["criterios_key"]
    dsm_entry = DSM5_DATA["transtornos_de_ansiedade"][criterios_key]
    criterios = dsm_entry["criterios"]
    descriptions = {}

    for code, value in criterios.items():
        if isinstance(value, str):
            descriptions[code] = value
        elif isinstance(value, dict):
            descriptions[code] = value.get("descricao", "")
            sintomas = value.get("sintomas", {})
            for sub_code, sub_val in sintomas.items():
                if isinstance(sub_val, str):
                    descriptions[sub_code] = sub_val
                elif isinstance(sub_val, dict):
                    descriptions[sub_code] = sub_val.get("descricao", "")
            for key in sorted(value.keys()):
                if key.startswith(("A", "B")) and key != "descricao" and key not in sintomas and len(key) > 1:
                    descriptions[key] = value[key]

    if transtorno in ("ansiedade_substancia", "ansiedade_medica"):
        descriptions["EXAMES"] = "Solicitação ou menção a exames laboratoriais, exames de imagem ou encaminhamento para investigação complementar"

    descriptions["RISCO"] = "Triagem de risco suicida e de autolesão"
    descriptions["EEM"] = "Exame do Estado Mental (aparência, comportamento, humor, afeto, pensamento, sensopercepção, consciência, orientação)"

    return descriptions


# ─── Dynamic Formative Tips Builder ─────────────────────────────────────────
def build_formative_tips(profile: dict, investigated: set, core_investigated: set, score_pct: int) -> list:
    transtorno = profile["transtorno"]
    tips = []

    # Universal tips
    if "RISCO" not in investigated:
        tips.append(
            "A triagem de risco suicida é obrigatória em toda avaliação psiquiátrica, "
            "mesmo em quadros de ansiedade. Pergunte diretamente sobre pensamentos de morte, "
            "desejo de morrer ou autolesão."
        )
    if "EEM" not in investigated:
        tips.append(
            "O Exame do Estado Mental (EEM) é parte fundamental da avaliação. "
            "Observe e descreva: aparência, comportamento, humor (relatado pelo paciente), "
            "afeto (observado por você), pensamento (forma e conteúdo), sensopercepção, "
            "consciência e orientação."
        )

    # Disorder-specific tips
    if transtorno == "TAG":
        if "E" not in investigated:
            tips.append("Sempre investigue uso de substâncias (cafeína, álcool, drogas) e condições médicas (hipotireoidismo, feocromocitoma) que possam mimetizar ou agravar sintomas de ansiedade.")
        if "F" not in investigated:
            tips.append("Considere diagnósticos diferenciais: os sintomas poderiam ser melhor explicados por outro transtorno (Pânico, Fobia Social, TOC, TEPT)?")
        if "A" not in investigated:
            tips.append("É essencial investigar a natureza e abrangência da preocupação: sobre quais temas o paciente se preocupa? São múltiplos? A preocupação é desproporcional?")
        if "B" not in investigated:
            tips.append("Pergunte se o paciente consegue controlar a preocupação. A dificuldade de controle é um critério central do TAG.")

    elif transtorno == "panico":
        if "A" not in investigated:
            tips.append("Investigue detalhadamente os ataques: início súbito, sintomas físicos (palpitação, sudorese, tremor, falta de ar), duração, pico em minutos. São critérios essenciais do Transtorno de Pânico.")
        if "B" not in investigated:
            tips.append("Avalie se houve mudança comportamental após os ataques: preocupação com novos ataques, evitação de situações, ida repetida ao pronto-socorro.")
        if "C" not in investigated:
            tips.append("Exclua causas orgânicas: hipertireoidismo, arritmias, uso de substâncias estimulantes podem causar sintomas semelhantes ao pânico.")

    elif transtorno == "ansiedade_social":
        if "A" not in investigated:
            tips.append("Investigue quais situações sociais provocam medo: apresentações, conversas, comer em público, ser observado. A especificidade ajuda no diagnóstico.")
        if "B" not in investigated:
            tips.append("Explore o que o paciente teme que aconteça nas situações sociais: ser julgado, humilhado, rejeitado. O medo de avaliação negativa é central.")
        if "D" not in investigated:
            tips.append("Avalie o padrão de evitação: o paciente evita as situações ou as suporta com sofrimento intenso? Isso é critério diagnóstico.")

    elif transtorno == "fobia_especifica":
        if "A" not in investigated:
            tips.append("Identifique o objeto/situação específica que provoca medo: sangue, agulhas, alturas, animais, etc. A especificidade é fundamental para o diagnóstico.")
        if "B" not in investigated:
            tips.append("Avalie se a exposição ao estímulo fóbico provoca resposta IMEDIATA de medo/ansiedade. Na fobia de sangue-injeção-ferimentos, a resposta vasovagal (desmaio) é característica.")
        if "F" not in investigated:
            tips.append("Avalie o impacto funcional: a fobia está causando prejuízo na vida do paciente (evita exames médicos, evita atividades)?")

    elif transtorno == "agorafobia":
        if "A" not in investigated:
            tips.append("Investigue medo/evitação em pelo menos 2 das 5 situações: transporte público, espaços abertos, locais fechados, filas/multidões, sair de casa sozinho.")
        if "B" not in investigated:
            tips.append("Explore a cognição por trás da evitação: o paciente teme não conseguir escapar ou não ter ajuda disponível caso passe mal?")
        if "D" not in investigated:
            tips.append("Avalie estratégias de segurança: o paciente precisa de acompanhante? Evita ativamente as situações? Estas são evidências importantes.")

    elif transtorno == "ansiedade_separacao":
        if "A" not in investigated:
            tips.append("Investigue os sintomas de separação: sofrimento ao se afastar, preocupação com perda/perigo das figuras de apego, pesadelos, recusa a sair, sintomas somáticos. São necessários pelo menos 3 sintomas.")
        if "B" not in investigated:
            tips.append("Avalie a duração: em adultos, os sintomas devem persistir por 6 meses ou mais. Investigue a temporalidade.")

    elif transtorno == "mutismo_seletivo":
        if "A" not in investigated:
            tips.append("Investigue em quais contextos a criança fala e em quais não fala. O padrão seletivo (fala em casa, não fala na escola/com estranhos) é o critério central.")
        if "B" not in investigated:
            tips.append("Avalie o impacto educacional e social: o mutismo interfere no desempenho escolar? Na socialização com colegas?")
        if "D" not in investigated:
            tips.append("Exclua que o fracasso para falar se deva a desconhecimento do idioma ou desconforto com ele. Verifique fluência no idioma em contextos onde a criança fala.")

    elif transtorno == "ansiedade_substancia":
        if "EXAMES" not in investigated:
            tips.append(
                "IMPORTANTE: Neste caso, a investigação de substâncias é crucial. Sempre pergunte "
                "ESPECIFICAMENTE sobre: cafeína (quantidade de café/dia), medicamentos de venda livre "
                "(descongestionantes, suplementos), álcool (frequência e quantidade), e considere "
                "solicitar exames laboratoriais (toxicológico, função hepática) para confirmar."
            )
        if "B" not in investigated:
            tips.append("Investigue a relação temporal entre o uso de substâncias e o início/piora dos sintomas. Essa conexão é fundamental para o diagnóstico de ansiedade induzida por substância.")

    elif transtorno == "ansiedade_medica":
        if "EXAMES" not in investigated:
            tips.append(
                "IMPORTANTE: Os sintomas deste paciente sugerem causa orgânica (taquicardia + perda de peso "
                "+ intolerância ao calor + tremores = padrão clássico de hipertireoidismo). Sempre considere "
                "solicitar exames laboratoriais (TSH, T4 livre, hemograma) quando os sintomas ansiosos "
                "acompanham sinais sistêmicos. A investigação de causa orgânica é essencial."
            )
        if "B" not in investigated:
            tips.append("Investigue sinais e sintomas que sugiram causa orgânica: perda de peso, intolerância ao calor, tremores finos, taquicardia em repouso. Esses achados devem levantar suspeita de condição médica subjacente.")

    # General low-coverage tip
    if len(core_investigated) < len(CORE_CRITERIA_MAP.get(transtorno, set())) * 0.4:
        tips.append("Utilize perguntas abertas para explorar o quadro clínico de forma mais ampla antes de partir para perguntas fechadas e direcionadas.")

    if score_pct >= 80:
        tips.append("Boa cobertura dos critérios diagnósticos. Continue praticando a formulação de hipóteses diagnósticas integrando os achados da anamnese.")

    return tips


# ─── Global Interview Quality Assessment Prompt (end of session) ─────────────
QUALITY_ASSESSMENT_PROMPT = """Você é um avaliador pedagógico especializado em ensino de anamnese psiquiátrica. Analise a transcrição COMPLETA da entrevista entre um estudante de Medicina e um paciente simulado.

Avalie a qualidade PROCESSUAL da entrevista nas seguintes dimensões:

1. **Acolhimento e Rapport**: O estudante demonstrou empatia? Usou linguagem acolhedora? Construiu vínculo terapêutico? Demonstrou escuta ativa?

2. **Progressão Lógica**: A entrevista seguiu uma sequência coerente? Partiu da queixa principal para aprofundamento? Houve organização no raciocínio clínico?

3. **Exploração Temporal**: O estudante investigou há quanto tempo os sintomas existem? Investigou início, evolução, fatores de melhora/piora? Buscou estabelecer uma linha do tempo?

4. **Aprofundamento Fenomenológico**: O estudante foi além das respostas superficiais? Fez perguntas de seguimento para entender melhor os sintomas? Explorou a experiência subjetiva do paciente?

5. **Linguagem e Comunicação**: O estudante usou linguagem adequada ao paciente? Evitou jargões? Fez perguntas abertas antes de fechadas? As perguntas foram claras?

6. **Construção de Vínculo**: O estudante validou as emoções do paciente? Demonstrou interesse genuíno? O paciente pareceu confortável durante a entrevista?

Para CADA dimensão, classifique como:
- "adequado": O estudante demonstrou competência nesta dimensão
- "parcial": Houve tentativa, mas com lacunas significativas
- "insuficiente": Não houve demonstração relevante desta competência
- "não_aplicável": A entrevista foi muito curta para avaliar

Responda APENAS com um JSON válido:
{
  "acolhimento_rapport": {"classificacao": "adequado|parcial|insuficiente|não_aplicável", "observacao": "breve comentário"},
  "progressao_logica": {"classificacao": "...", "observacao": "..."},
  "exploracao_temporal": {"classificacao": "...", "observacao": "..."},
  "aprofundamento_fenomenologico": {"classificacao": "...", "observacao": "..."},
  "linguagem_comunicacao": {"classificacao": "...", "observacao": "..."},
  "construcao_vinculo": {"classificacao": "...", "observacao": "..."},
  "avaliacao_global": "breve parágrafo resumindo os pontos fortes e as áreas de melhoria do estudante nesta entrevista",
  "sugestoes_formativas": ["lista de 3-5 sugestões práticas e específicas para o estudante melhorar"]
}"""


# ─── EEM Summary Prompt ──────────────────────────────────────────────────────
EEM_SUMMARY_PROMPT = """Você é um sistema que analisa transcrições de entrevistas psiquiátricas simuladas. Extraia TODAS as pistas observacionais das respostas do paciente (especialmente tudo entre asteriscos *...*) e organize em categorias do Exame do Estado Mental.

Para cada categoria, liste as observações encontradas na conversa, citando os trechos relevantes. Se não houver dados, escreva "Sem dados observados na entrevista".

Categorias:
1. **Aparência Geral e Vestuário**: Como o paciente se apresentou visualmente (roupas, higiene, postura ao entrar)
2. **Comportamento Psicomotor**: Gestos, agitação, lentificação, inquietação, movimentos repetitivos
3. **Expressão Facial e Contato Visual**: Expressões observadas, mudanças, contato visual ou evitação
4. **Atitude na Entrevista**: Cooperativo, resistente, hostil, evasivo, desconfiado
5. **Fala e Linguagem**: Velocidade, volume, prosódia, pausas, hesitações
6. **Humor e Afeto Observados**: Sinais emocionais (choro, tensão, sorrisos, etc.)

Responda em texto formatado, com cada categoria como título em negrito. Seja objetivo e cite diretamente os trechos observacionais encontrados entre aspas."""


# ─── Voice Mapping for TTS (ElevenLabs voices) ──────────────────────────────
# Each patient gets a distinct voice matching gender, age, and emotional profile.
# Using ElevenLabs voices via the generate_audio helper.
PATIENT_VOICE_MAP = {
    "Márcia":     {"voice": "charlotte", "model": "elevenlabs_tts_v3"},  # TAG: feminino, meia-idade, voz grave e preocupada
    "Fernando":   {"voice": "daniel",    "model": "elevenlabs_tts_v3"},  # Pânico: masculino, adulto, voz tensa e aflita
    "Beatriz":    {"voice": "alice",     "model": "elevenlabs_tts_v3"},  # Ansiedade Social: feminino, jovem, voz baixa e retraída
    "Lucas":      {"voice": "clyde",     "model": "elevenlabs_tts_v3"},  # Fobia Específica: masculino, jovem-adulto, voz grave e nervosa
    "Helena":     {"voice": "nicole",    "model": "elevenlabs_tts_v3"},  # Agorafobia: feminino, idosa, voz cansada e temerosa
    "Rafael":     {"voice": "bill",      "model": "elevenlabs_tts_v3"},  # Ansiedade Separação: masculino, adulto, voz insegura e grave
    "Sofia":      {"voice": "sarah",     "model": "elevenlabs_tts_v3"},  # Mutismo Seletivo: feminino, mãe Dona Lúcia, voz contida
    "Jorge":      {"voice": "dave",      "model": "elevenlabs_tts_v3"},  # Substância: masculino, meia-idade, voz rouca e irritável
    "Dona Célia": {"voice": "dorothy",   "model": "elevenlabs_tts_v3"},  # Condição Médica: feminino, idosa, voz frágil e cansada
}

# Emotional prompting prefixes — ElevenLabs v3 adapts vocal tone to these cues.
# Prepended to the dialogue text before TTS synthesis for more realistic delivery.
PATIENT_EMOTION_PREFIX = {
    "Márcia":     "(falando devagar, com voz cansada e preocupada) ",
    "Fernando":   "(falando com voz trêmula, tenso, com medo) ",
    "Beatriz":    "(falando muito baixo, tímida, quase sussurrando) ",
    "Lucas":      "(falando com voz nervosa e hesitante) ",
    "Helena":     "(falando devagar, com voz fraca e temerosa) ",
    "Rafael":     "(falando com voz insegura, triste) ",
    "Sofia":      "(falando com preocupação de mãe, voz contida) ",
    "Jorge":      "(falando com voz grave, irritável, cansado) ",
    "Dona Célia": "(falando devagar, com voz frágil e sofrida) ",
}


def extract_dialogue_text(text: str) -> str:
    """Extract only the spoken dialogue from a patient response.
    Removes behavioral descriptions between asterisks (*...*) and
    stage directions between brackets ([...]).
    Returns the spoken text only, suitable for TTS.
    """
    # Remove text between asterisks (behavioral descriptions)
    cleaned = re.sub(r'\*[^*]+\*', '', text)
    # Remove text between brackets (stage directions for Sofia/mutismo)
    cleaned = re.sub(r'\[[^\]]+\]', '', cleaned)
    # Clean up extra whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Remove leading/trailing dashes or quotes that might remain
    cleaned = cleaned.strip('— -')
    return cleaned


# ─── FastAPI App ─────────────────────────────────────────────────────────────
app = FastAPI(title="PsiqMentor API v5.5 — Avatar Interativo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Anthropic()

# In-memory session store
sessions: dict = {}

# Persistent survey storage path
# Use /data on Render (persistent disk), fallback to local ./data
SURVEY_DIR = Path("/data") if Path("/data").exists() and os.access("/data", os.W_OK) else Path(__file__).parent / "data"
SURVEY_FILE = SURVEY_DIR / "surveys.json"
SESSION_COUNT_FILE = SURVEY_DIR / "session_count.json"

# ─── Admin Config ────────────────────────────────────────────────────────────
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "Mestrado2026")
ADMIN_TOKENS: dict = {}  # token -> expiry timestamp


def verify_admin_token(token: str) -> bool:
    """Verify admin token is valid and not expired."""
    if not token or token not in ADMIN_TOKENS:
        return False
    if time.time() > ADMIN_TOKENS[token]:
        del ADMIN_TOKENS[token]
        return False
    return True


# ─── Request/Response Models ────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str
    message: str


class FinishRequest(BaseModel):
    session_id: str


class SurveyRequest(BaseModel):
    session_id: str
    responses: dict  # Q1-Q10 (int 1-5), Q11-Q12 (str)


class EEMRequest(BaseModel):
    session_id: str


class EEMSubmitRequest(BaseModel):
    session_id: str
    eem_data: dict


class TTSRequest(BaseModel):
    session_id: str
    text: str


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class LiveAvatarTokenRequest(BaseModel):
    session_id: str  # our PsiqMentor session ID


class LiveAvatarStartRequest(BaseModel):
    session_token: str


# ─── Endpoints ──────────────────────────────────────────────────────────────
@app.post("/api/start")
def start_session(patient: str = Query(default=None)):
    """Start a new simulation session.
    If ?patient=Helena is provided, use that specific patient (for avatar mode).
    Otherwise, pick a random patient from all profiles.
    """
    session_id = str(uuid.uuid4())
    if patient:
        matching = [p for p in PATIENT_PROFILES if p["nome"].lower() == patient.lower()]
        if not matching:
            raise HTTPException(status_code=400, detail=f"Paciente '{patient}' não encontrado")
        profile = matching[0]
    else:
        profile = random.choice(PATIENT_PROFILES)
    system_prompt = build_system_prompt(profile)
    tracker_prompt = build_tracker_prompt(profile)

    sessions[session_id] = {
        "profile": profile,
        "system_prompt": system_prompt,
        "tracker_prompt": tracker_prompt,
        "messages": [],
        "criteria_tracked": {},
        "criteria_log": [],
        "started_at": datetime.now().isoformat(),
        "finished": False,
        "eem_student": None,
    }

    # Increment persistent session counter
    SURVEY_DIR.mkdir(parents=True, exist_ok=True)
    count_data = {"total": 0}
    if SESSION_COUNT_FILE.exists():
        try:
            count_data = json.loads(SESSION_COUNT_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            count_data = {"total": 0}
    count_data["total"] += 1
    SESSION_COUNT_FILE.write_text(json.dumps(count_data), encoding="utf-8")

    return {
        "session_id": session_id,
        "patient_name": profile["nome"],
        "patient_age": profile["idade"],
        "patient_gender": profile["genero"],
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Send a message and get the patient's response. Tracking is silent."""
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if session["finished"]:
        raise HTTPException(status_code=400, detail="Sessão já finalizada")

    # Add user message to history
    session["messages"].append({"role": "user", "content": req.message})

    # 1. Get patient response from LLM
    try:
        patient_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=session["system_prompt"],
            messages=session["messages"],
        )
        assistant_text = patient_response.content[0].text
    except Exception as e:
        # Remove the message we just added so session stays clean
        session["messages"].pop()
        raise HTTPException(status_code=502, detail=f"Erro na API Anthropic: {type(e).__name__}: {str(e)}")


    # Add assistant response to history
    session["messages"].append({"role": "assistant", "content": assistant_text})

    # 2. Silent criteria tracking (backend only)
    tracking_messages = [
        {
            "role": "user",
            "content": (
                f"Contexto da conversa até agora:\n"
                f"{_format_conversation(session['messages'][:-1])}\n\n"
                f"ÚLTIMA PERGUNTA DO ALUNO:\n{req.message}"
            ),
        }
    ]

    try:
        tracking_response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=session["tracker_prompt"],
            messages=tracking_messages,
        )
        tracking_text = tracking_response.content[0].text

        json_start = tracking_text.find("{")
        json_end = tracking_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            tracking_data = json.loads(tracking_text[json_start:json_end])
        else:
            tracking_data = {
                "criterios_investigados": [],
                "justificativa": "Não foi possível analisar",
            }
    except Exception:
        tracking_data = {
            "criterios_investigados": [],
            "justificativa": "Erro na análise",
        }

    # Update criteria tracking (stored silently)
    for criterio in tracking_data.get("criterios_investigados", []):
        if criterio not in session["criteria_tracked"]:
            session["criteria_tracked"][criterio] = {
                "first_asked_turn": len(session["messages"]) // 2,
            }

    session["criteria_log"].append(
        {
            "turn": len(session["messages"]) // 2,
            "student_message": req.message,
            "criteria": tracking_data.get("criterios_investigados", []),
            "justificativa": tracking_data.get("justificativa", ""),
        }
    )

    return {
        "patient_response": assistant_text,
    }


@app.post("/api/chat-stream")
async def chat_stream(req: ChatRequest):
    """Stream patient response word-by-word via SSE, then auto-generate TTS.
    Events:
      data: {"type":"token", "text":"word "}
      data: {"type":"done", "full_text":"..."}
      data: {"type":"audio", "audio_b64":"..."}
      data: {"type":"audio_error", "detail":"..."}
    """
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sess\u00e3o n\u00e3o encontrada")
    if session["finished"]:
        raise HTTPException(status_code=400, detail="Sess\u00e3o j\u00e1 finalizada")

    session["messages"].append({"role": "user", "content": req.message})

    async def event_generator():
        full_text = ""
        try:
            # Stream patient response
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=500,
                system=session["system_prompt"],
                messages=session["messages"],
            ) as stream:
                for text_chunk in stream.text_stream:
                    full_text += text_chunk
                    yield f"data: {json.dumps({'type': 'token', 'text': text_chunk}, ensure_ascii=False)}\n\n"

        except Exception as e:
            session["messages"].pop()
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)}, ensure_ascii=False)}\n\n"
            return

        # Save to history
        session["messages"].append({"role": "assistant", "content": full_text})

        # Send "done" event with full text
        yield f"data: {json.dumps({'type': 'done', 'full_text': full_text}, ensure_ascii=False)}\n\n"

        # Silent tracking (fire-and-forget, non-blocking)
        try:
            tracking_messages = [
                {
                    "role": "user",
                    "content": (
                        f"Contexto da conversa at\u00e9 agora:\n"
                        f"{_format_conversation(session['messages'][:-1])}\n\n"
                        f"\u00daLTIMA PERGUNTA DO ALUNO:\n{req.message}"
                    ),
                }
            ]
            tracking_response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                system=session["tracker_prompt"],
                messages=tracking_messages,
            )
            tracking_text = tracking_response.content[0].text
            json_start = tracking_text.find("{")
            json_end = tracking_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                tracking_data = json.loads(tracking_text[json_start:json_end])
            else:
                tracking_data = {"criterios_investigados": [], "justificativa": ""}
        except Exception:
            tracking_data = {"criterios_investigados": [], "justificativa": ""}

        for criterio in tracking_data.get("criterios_investigados", []):
            if criterio not in session["criteria_tracked"]:
                session["criteria_tracked"][criterio] = {
                    "first_asked_turn": len(session["messages"]) // 2,
                }
        session["criteria_log"].append({
            "turn": len(session["messages"]) // 2,
            "student_message": req.message,
            "criteria": tracking_data.get("criterios_investigados", []),
            "justificativa": tracking_data.get("justificativa", ""),
        })

        # Generate TTS audio inline and send as final event
        try:
            patient_name = session["profile"]["nome"]
            voice_config = PATIENT_VOICE_MAP.get(patient_name, {"voice": "charlotte", "model": "elevenlabs_tts_v3"})
            emotion_prefix = PATIENT_EMOTION_PREFIX.get(patient_name, "")
            dialogue_text = extract_dialogue_text(full_text)
            if dialogue_text and len(dialogue_text.strip()) >= 2:
                tts_text = emotion_prefix + dialogue_text
                audio_bytes = await generate_audio(
                    tts_text,
                    voice=voice_config["voice"],
                    model=voice_config["model"],
                )
                import base64 as b64mod
                audio_b64 = b64mod.b64encode(audio_bytes).decode("ascii")
                yield f"data: {json.dumps({'type': 'audio', 'audio_b64': audio_b64})}\n\n"
            else:
                no_dialog_msg = json.dumps({'type': 'audio_error', 'detail': 'Sem diálogo para TTS'})
                yield f"data: {no_dialog_msg}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'audio_error', 'detail': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── LiveAvatar Endpoints ──────────────────────────────────────────────────
@app.post("/api/liveavatar-token")
async def create_liveavatar_token(req: LiveAvatarTokenRequest):
    """Create a LiveAvatar session token for Helena avatar.
    This keeps the LIVEAVATAR_API_KEY server-side (never exposed to frontend).
    """
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    # Only allow for Helena (avatar mode)
    if session["profile"]["nome"] != "Helena":
        raise HTTPException(status_code=400, detail="LiveAvatar disponível apenas para Helena")

    # ── Denis: configure estas variáveis de ambiente no Render ──
    LIVEAVATAR_API_KEY = os.environ.get("LIVEAVATAR_API_KEY", "")
    if not LIVEAVATAR_API_KEY:
        raise HTTPException(status_code=500, detail="LIVEAVATAR_API_KEY não configurada")

    # Avatar ID for "Marianne Sitting" — retrieve from GET /v1/avatars/public
    AVATAR_ID = os.environ.get("LIVEAVATAR_AVATAR_ID", "")
    if not AVATAR_ID:
        raise HTTPException(status_code=500, detail="LIVEAVATAR_AVATAR_ID não configurado")

    # Voice ID for Portuguese female voice — retrieve from GET /v1/voices
    VOICE_ID = os.environ.get("LIVEAVATAR_VOICE_ID", "")

    # Build token creation payload — FULL mode WITHOUT context_id
    # This means LiveAvatar handles VAD/STT/WebRTC but NOT LLM responses.
    # Our Claude backend remains the "brain" of the patient.
    token_payload = {
        "mode": "FULL",
        "avatar_id": AVATAR_ID,
        "video_settings": {
            "quality": "high",
            "encoding": "H264",
        },
    }

    # Add avatar_persona with voice but NO context_id
    if VOICE_ID:
        token_payload["avatar_persona"] = {
            "voice_id": VOICE_ID,
            "language": "pt",
        }

    async with httpx.AsyncClient(timeout=30) as http_client:
        resp = await http_client.post(
            "https://api.liveavatar.com/v1/sessions/token",
            headers={
                "X-API-KEY": LIVEAVATAR_API_KEY,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=token_payload,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"LiveAvatar API error ({resp.status_code}): {resp.text}",
            )

        la_data = resp.json()

    return {
        "session_token": la_data["data"]["session_token"],
        "session_id_la": la_data["data"]["session_id"],
    }


@app.post("/api/liveavatar-start")
async def start_liveavatar(req: LiveAvatarStartRequest):
    """Start a LiveAvatar session and return LiveKit connection details."""
    async with httpx.AsyncClient(timeout=30) as http_client:
        resp = await http_client.post(
            "https://api.liveavatar.com/v1/sessions/start",
            headers={
                "accept": "application/json",
                "authorization": f"Bearer {req.session_token}",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"LiveAvatar start error ({resp.status_code}): {resp.text}",
            )

        return resp.json()


# ─── TTS Endpoint ──────────────────────────────────────────────────────────
@app.post("/api/tts")
async def text_to_speech(req: TTSRequest):
    """Convert patient response text to speech audio.
    Strips behavioral descriptions (*...*) and stage directions ([...]),
    then synthesizes only the spoken dialogue.
    Returns MP3 audio bytes.
    """
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    # Extract spoken dialogue only
    dialogue_text = extract_dialogue_text(req.text)
    if not dialogue_text or len(dialogue_text.strip()) < 2:
        raise HTTPException(status_code=400, detail="Nenhum texto de diálogo encontrado na resposta")

    # Get patient voice config and emotional prefix
    patient_name = session["profile"]["nome"]
    voice_config = PATIENT_VOICE_MAP.get(patient_name, {"voice": "charlotte", "model": "elevenlabs_tts_v3"})
    emotion_prefix = PATIENT_EMOTION_PREFIX.get(patient_name, "")
    tts_text = emotion_prefix + dialogue_text

    try:
        audio_bytes = await generate_audio(
            tts_text,
            voice=voice_config["voice"],
            model=voice_config["model"],
        )
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=response.mp3"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar áudio: {type(e).__name__}: {str(e)}")


@app.post("/api/eem-summary")
def eem_summary(req: EEMRequest):
    """Generate EEM observational summary from conversation cues."""
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    conversation_text = _format_full_conversation(session["messages"])

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=EEM_SUMMARY_PROMPT,
            messages=[{"role": "user", "content": f"TRANSCRIÇÃO DA ENTREVISTA:\n\n{conversation_text}"}],
        )
        return {"eem_summary": response.content[0].text}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao gerar resumo do EEM: {str(e)}")


@app.post("/api/eem-submit")
def eem_submit(req: EEMSubmitRequest):
    """Store the student's EEM form data in the session."""
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    session["eem_student"] = req.eem_data
    return {"status": "ok"}


@app.post("/api/finish")
def finish_session(req: FinishRequest):
    """Finish the session and return the full formative feedback report."""
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    session["finished"] = True
    profile = session["profile"]
    transtorno = profile["transtorno"]

    # ── Build dynamic criteria list ──────────────────────────────────────
    all_criteria = CRITERIA_MAP.get(transtorno, []) + ["RISCO", "EEM"]
    # Deduplicate while preserving order (RISCO/EEM may already be in the list)
    seen = set()
    unique_criteria = []
    for c in all_criteria:
        if c not in seen:
            seen.add(c)
            unique_criteria.append(c)
    all_criteria = unique_criteria

    investigated = set(session["criteria_tracked"].keys())
    missing = [c for c in all_criteria if c not in investigated]

    core_criteria = CORE_CRITERIA_MAP.get(transtorno, set())
    core_investigated = core_criteria.intersection(investigated)
    score_pct = round((len(core_investigated) / max(len(core_criteria), 1)) * 100)

    # ── Global quality assessment ────────────────────────────────────────
    quality_assessment = _assess_interview_quality(session["messages"])

    # ── Build dynamic criteria descriptions ──────────────────────────────
    criteria_descriptions = build_criteria_descriptions(profile)

    # ── Build formative tips ─────────────────────────────────────────────
    formative_tips = build_formative_tips(profile, investigated, core_investigated, score_pct)

    # ── EEM evaluation (if student completed it) ─────────────────────────
    eem_evaluation = None
    if session.get("eem_student"):
        eem_evaluation = _evaluate_eem(session)

    return {
        "score_pct": score_pct,
        "criteria_investigated": sorted(list(investigated)),
        "criteria_missing": missing,
        "criteria_descriptions": criteria_descriptions,
        "total_turns": len(session["messages"]) // 2,
        "diagnostico_correto": profile["diagnostico_real"],
        "transtorno": transtorno,
        "quality_assessment": quality_assessment,
        "formative_tips": formative_tips,
        "criteria_log": session["criteria_log"],
        "eem_student": session.get("eem_student"),
        "eem_evaluation": eem_evaluation,
    }


# ─── Survey Endpoints ───────────────────────────────────────────────────────
@app.post("/api/survey")
def submit_survey(req: SurveyRequest):
    """Save survey responses to persistent storage."""
    session = sessions.get(req.session_id)
    transtorno = session["profile"]["transtorno"] if session else "unknown"

    entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": req.session_id,
        "transtorno": transtorno,
        "responses": req.responses,
    }

    # Ensure directory exists
    SURVEY_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing data, append, write back
    surveys = []
    if SURVEY_FILE.exists():
        try:
            surveys = json.loads(SURVEY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            surveys = []

    surveys.append(entry)
    SURVEY_FILE.write_text(json.dumps(surveys, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"status": "ok"}


@app.get("/api/survey/export")
def export_surveys():
    """Export all survey responses as a CSV file."""
    surveys = []
    if SURVEY_FILE.exists():
        try:
            surveys = json.loads(SURVEY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            surveys = []

    output = io.StringIO()
    fieldnames = ["timestamp", "session_id", "transtorno",
                  "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10",
                  "Q11", "Q12"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for s in surveys:
        row = {
            "timestamp": s.get("timestamp", ""),
            "session_id": s.get("session_id", ""),
            "transtorno": s.get("transtorno", ""),
        }
        responses = s.get("responses", {})
        for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10", "Q11", "Q12"]:
            row[q] = responses.get(q, "")
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=surveys.csv"},
    )


# ─── Admin Endpoints ─────────────────────────────────────────────────────────
@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    """Authenticate admin user and return a session token."""
    if req.username != ADMIN_USER or req.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    token = secrets.token_hex(32)
    ADMIN_TOKENS[token] = time.time() + 7200  # 2 hours
    return {"token": token, "expires_in": 7200}


@app.get("/api/admin/dashboard")
def admin_dashboard(token: str = Query(...)):
    """Return system health, survey stats, and session info."""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # Session counter
    total_sessions = 0
    if SESSION_COUNT_FILE.exists():
        try:
            total_sessions = json.loads(SESSION_COUNT_FILE.read_text(encoding="utf-8")).get("total", 0)
        except (json.JSONDecodeError, OSError):
            total_sessions = 0

    # Survey stats
    surveys = []
    if SURVEY_FILE.exists():
        try:
            surveys = json.loads(SURVEY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            surveys = []

    transtorno_counts = {}
    for s in surveys:
        t = s.get("transtorno", "unknown")
        transtorno_counts[t] = transtorno_counts.get(t, 0) + 1

    return {
        "health": {
            "version": "v5.5",
            "anthropic_key_set": has_key,
            "active_sessions": len(sessions),
            "total_sessions": total_sessions,
        },
        "surveys": {
            "total": len(surveys),
            "by_transtorno": transtorno_counts,
        },
    }


@app.post("/api/admin/test-anthropic")
def admin_test_anthropic(token: str = Query(...)):
    """Test Anthropic API connectivity with a minimal call."""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    start_time = time.time()
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "Olá"}],
        )
        elapsed = round((time.time() - start_time) * 1000)
        return {
            "status": "ok",
            "response_time_ms": elapsed,
            "model": "claude-haiku-4-5",
            "message": response.content[0].text[:50],
        }
    except Exception as e:
        elapsed = round((time.time() - start_time) * 1000)
        return {
            "status": "error",
            "response_time_ms": elapsed,
            "error": f"{type(e).__name__}: {str(e)}",
        }


@app.get("/api/admin/survey/data")
def admin_survey_data(token: str = Query(...)):
    """Return all survey responses as JSON."""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    surveys = []
    if SURVEY_FILE.exists():
        try:
            surveys = json.loads(SURVEY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            surveys = []
    return {"surveys": surveys}


@app.post("/api/admin/survey/clear")
def admin_survey_clear(token: str = Query(...)):
    """Clear all survey data (for removing test data before real collection)."""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    if SURVEY_FILE.exists():
        SURVEY_FILE.unlink()
    return {"status": "ok", "message": "Dados de pesquisa removidos com sucesso"}


# ─── Health Check ───────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "version": "v5.5",
        "active_sessions": len(sessions),
        "anthropic_key_set": has_key,
    }


# ─── Serve Frontend ─────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = Path(__file__).parent / "index.html"
    content = html_path.read_text(encoding="utf-8")
    content = content.replace("__PORT_8000__", "")
    return HTMLResponse(content=content)


# ─── Helper Functions ───────────────────────────────────────────────────────
def _assess_interview_quality(messages: list) -> dict:
    """Assess the overall quality of the interview process."""
    if len(messages) < 4:  # Less than 2 turns
        return {
            "acolhimento_rapport": {"classificacao": "não_aplicável", "observacao": "Entrevista muito curta para avaliar."},
            "progressao_logica": {"classificacao": "não_aplicável", "observacao": "Entrevista muito curta para avaliar."},
            "exploracao_temporal": {"classificacao": "não_aplicável", "observacao": "Entrevista muito curta para avaliar."},
            "aprofundamento_fenomenologico": {"classificacao": "não_aplicável", "observacao": "Entrevista muito curta para avaliar."},
            "linguagem_comunicacao": {"classificacao": "não_aplicável", "observacao": "Entrevista muito curta para avaliar."},
            "construcao_vinculo": {"classificacao": "não_aplicável", "observacao": "Entrevista muito curta para avaliar."},
            "avaliacao_global": "A entrevista foi muito breve para uma avaliação processual significativa.",
            "sugestoes_formativas": ["Realize entrevistas mais longas para permitir uma avaliação adequada da qualidade processual."],
        }

    conversation_text = _format_full_conversation(messages)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=QUALITY_ASSESSMENT_PROMPT,
            messages=[{"role": "user", "content": f"TRANSCRIÇÃO DA ENTREVISTA:\n\n{conversation_text}"}],
        )
        response_text = response.content[0].text

        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return json.loads(response_text[json_start:json_end])
    except Exception:
        pass

    return {
        "acolhimento_rapport": {"classificacao": "não_aplicável", "observacao": "Não foi possível avaliar."},
        "progressao_logica": {"classificacao": "não_aplicável", "observacao": "Não foi possível avaliar."},
        "exploracao_temporal": {"classificacao": "não_aplicável", "observacao": "Não foi possível avaliar."},
        "aprofundamento_fenomenologico": {"classificacao": "não_aplicável", "observacao": "Não foi possível avaliar."},
        "linguagem_comunicacao": {"classificacao": "não_aplicável", "observacao": "Não foi possível avaliar."},
        "construcao_vinculo": {"classificacao": "não_aplicável", "observacao": "Não foi possível avaliar."},
        "avaliacao_global": "Não foi possível realizar a avaliação processual.",
        "sugestoes_formativas": [],
    }


def _format_conversation(messages: list) -> str:
    """Format last messages for criteria tracker."""
    lines = []
    for m in messages[-10:]:
        role = "Aluno" if m["role"] == "user" else "Paciente"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)


def _format_full_conversation(messages: list) -> str:
    """Format the full conversation for quality assessment."""
    lines = []
    for m in messages:
        role = "Estudante" if m["role"] == "user" else "Paciente"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)


def _evaluate_eem(session: dict) -> dict:
    """Evaluate the student's EEM against conversational observations."""
    eem_data = session.get("eem_student", {})
    conversation_text = _format_full_conversation(session["messages"])

    prompt = f"""Analise o Exame do Estado Mental preenchido por um estudante de medicina após entrevista com paciente simulado.

EEM DO ESTUDANTE:
{json.dumps(eem_data, ensure_ascii=False, indent=2)}

TRANSCRIÇÃO DA ENTREVISTA:
{conversation_text}

Avalie: O estudante captou adequadamente as pistas observacionais? Identificou corretamente o humor e afeto? Registrou o comportamento psicomotor observado? Suas descrições são coerentes com o que o paciente demonstrou?

Responda com JSON:
{{
  "avaliacao_geral": "adequado|parcial|insuficiente",
  "pontos_fortes": ["lista de pontos positivos"],
  "areas_melhorar": ["lista de áreas a melhorar"],
  "comentario": "breve parágrafo com feedback formativo"
}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            return json.loads(text[json_start:json_end])
    except Exception:
        pass
    return {
        "avaliacao_geral": "não_aplicável",
        "pontos_fortes": [],
        "areas_melhorar": [],
        "comentario": "Não foi possível avaliar o EEM.",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
