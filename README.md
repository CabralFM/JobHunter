# job-hunter

Busca diária de vagas na **Suíça romanda** e em **Luxemburgo**, match semântico com o perfil de
**dois candidatos**, geração de **CV e carta de motivação adaptados** para cada vaga, fila de
candidaturas e **registro de tudo num banco de dados local**.

```
 cron 07:15 ─► buscar ─► pré-filtro ─► triagem ─► avaliação ─► CV + carta ─► auditoria ─► fila/envio ─► relatório
             (LinkedIn,   (regex,      (Claude,    (Claude,      (Claude,      (Claude:     (você aprova   (HTML+CSV
              Indeed,      grátis)      barato)     2 notas)      no idioma     nada         ou e-mail     do dia)
              Google Jobs,                                        da vaga)      inventado?)  automático)
              jobs.ch,
              jobup.ch...)
                                         tudo gravado em data/jobhunter.db (SQLite)
```

---

## 1. O que já existe no GitHub (e por que não usar um só)

| Projeto | O que faz bem | Por que não resolve sozinho |
|---|---|---|
| [JobSpy](https://github.com/speedyapply/JobSpy) | Raspa LinkedIn, Indeed (tem CH e LU), Google Jobs, Glassdoor numa tabela. Sem login. | Só busca. Não tem jobs.ch/jobup.ch/moovijob. **Usado aqui como fonte.** |
| [AIHawk / Jobs_Applier_AI_Agent](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) (~30k ⭐) | Foi o mais famoso: aplicava sozinho no LinkedIn com CV gerado por LLM. | A parte de auto-apply **saiu do código aberto** e virou serviço pago (laboro.co). Focado em Easy Apply. |
| [career-ops](https://github.com/santifer/career-ops) | Pipeline dentro do Claude Code: avalia vaga (nota A–F em 10 dimensões), gera CV ATS por vaga, rastreia candidaturas. Ótimas ideias de avaliação. | Interativo (você conduz no terminal), um candidato, sem fontes suíças/luxemburguesas. **Inspirou o matcher daqui.** |
| [Resume-Matcher](https://github.com/srbhr/resume-matcher) | App web: CV mestre → CV adaptado por vaga, destaque de palavras-chave. | Não busca vagas, não agenda, não aplica. **Inspirou o conceito de perfil mestre.** |
| [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn), [LinkedIn-AI-Job-Applier-Ultimate](https://github.com/beatwad/LinkedIn-AI-Job-Applier-Ultimate), [ApplyPilot](https://github.com/ibarrajo/ApplyPilot) | Robôs de Easy Apply / formulários com Selenium/Playwright. | Só LinkedIn (ou ATS americanos), alto risco de bloqueio da conta. Podem ser plugados depois (ver §6). |
| [huma](https://github.com/Diem0n/huma), [cvmatcher](https://github.com/VeTamas/cvmatcher), [matchra](https://github.com/louispawaon/matchra) | Match CV×vaga com LLM local (Ollama) / embeddings. | Protótipos pequenos. **matchra** trouxe a ideia de não pedir a nota final ao LLM (ver §3). |
| Atores do [Apify](https://apify.com/store) para jobs.ch, jobup.ch, job-room.ch, moovijob, jobs.lu | Scrapers prontos e mantidos, ~US$ 0,5–2 por 1.000 vagas. | Pagos (pouco) e de terceiros. **Suportados aqui como fonte opcional.** |

**Conclusão:** nenhum projeto cobre o combo *Suíça romanda + Luxemburgo + 2 candidatos + match
que ignora o título + CV adaptado sem inventar + execução diária*. O caminho mais robusto foi um
pipeline próprio e enxuto (~1.000 linhas de Python) que **reaproveita** JobSpy e Apify para a
parte frágil (scraping) e usa o Claude para a parte inteligente (match, adaptação, auditoria).

> Por que não "só usar um agente" (Claude Code / OpenClaw / Grok) solto, todo dia? Um agente
> autônomo é ótimo para tarefas abertas, mas aqui o fluxo é conhecido e repetitivo. Um pipeline
> em código é mais **barato** (cada etapa usa o mínimo de modelo necessário), **previsível**
> (mesma entrada → mesma decisão, auditável no banco) e **não quebra em silêncio**. O LLM entra
> só onde precisa de julgamento. Nada impede, depois, de usar o Claude Code em cima do banco
> para perguntas ad-hoc ("quais vagas de banco em Genebra tiveram match > 80 este mês?").

---

## 2. Fontes de vagas

| Mercado | Site | Como | Status |
|---|---|---|---|
| CH + LU | **LinkedIn** | JobSpy (busca pública, sem login → sua conta não corre risco) | pronto |
| CH + LU | **Indeed** (ch./lu.indeed.com) | JobSpy | pronto |
| CH + LU | **Google Jobs** | JobSpy — agrega jobup, moovijob, jobs.lu, sites de empresas | pronto |
| CH | **jobs.ch** / **jobup.ch** (JobCloud; jobup = principal da Romandie) | API JSON interna do site | ⚠ **não verificado** — rode `probe` (ver §7) |
| CH | job-room.ch / arbeit.swiss (SECO, público) | Apify ou API oficial (pedir a jobroom-api@seco.admin.ch) | configurável |
| CH | JobScout24, Indeed já cobre | — | — |
| LU | **moovijob.com**, **jobs.lu**, jobfinder.lu, ADEM | Apify (`sources.apify` no settings) | configurável |
| LU | ictjob.lu, financejobs.lu (nichos TI/finanças) | Apify ou novo conector | futuro |

"jobapp.ch" não foi encontrado — se for outro site, me passe o link e vira um conector.

Adicionar um site = um arquivo em `jobhunter/sources/` com um método `search()` que devolve `Job`.

---

## 3. Como o match funciona (e por que assim)

### Problema 1: a vaga combina com você, mas o título é outro
Resolvido em **duas camadas**:
1. **Expansão de títulos** (`query_expansion.py`): o Claude lê o perfil mestre e gera 10–15
   termos de busca em FR/EN/DE que o mercado local realmente usa. Ex.: um Product Owner também
   busca *"Business Analyst IT"*, *"Chef de projet digital"*, *"Responsable de produit"*.
   A lista fica em `data/search_terms_<id>.json` — você pode editar e travar (`"locked": true`).
2. **Match pelo conteúdo** (`matcher.py`): a avaliação lê a **descrição inteira** e ignora o título.

### Problema 2: o CV atual está ajustado para outras vagas
Por isso cada vaga recebe **três notas**:

| Nota | Pergunta | Fonte |
|---|---|---|
| `score_current` | Um recrutador/ATS, lendo **o CV de hoje**, chamaria você? | `current_cv.md` |
| `score_potential` | E com um CV **reescrito só com fatos verdadeiros**? | `master_cv.md` |
| `desire_fit` | Essa vaga é algo que você **quer**? | `profile.yaml` |

Exemplo ilustrativo (números hipotéticos) do tipo de caso que isso captura: vaga de *"Coordinateur·rice de programmes"* numa
ONG em Genebra. CV atual (focado em TI bancária) → **current 46**. Mas o perfil mestre tem 3 anos
de gestão de projetos com financiamento europeu → **potential 81**. Um filtro por palavra-chave
descartaria; aqui ela vira candidatura com CV reescrito. A diferença `potential − current` aparece
no relatório como "ganho de adaptação".

### Notas por dimensão, soma em código
O Claude dá nota **por dimensão, com evidência** (hard skills 30%, experiência 20%, setor 15%,
idiomas 15%, local/permis 10%, formação 10%), e a nota final é calculada em Python com pesos
fixos. Pedir "dê uma nota de 0 a 100" direto ao LLM gera números que oscilam entre execuções;
dimensões com evidência + pesos determinísticos são mais estáveis e você vê de onde veio cada ponto.

Requisitos eliminatórios (ex.: *"allemand C1 indispensable"* para quem tem A2) viram
`knockout`, independentemente das notas.

### Limiares: por que não 60%?
Você sugeriu 60%. Com esse esquema, **60 de potencial = "metade dos requisitos"** — candidatura
fraca. Proposta inicial (em `settings.yaml`):

| Decisão | Regra | O que acontece |
|---|---|---|
| `apply` | potencial ≥ **72** **e** desejo ≥ **60** | gera CV + carta, entra na fila (ou é enviada) |
| `review` | potencial ≥ **58** | aparece no relatório para vocês decidirem |
| `skip` | resto | fica no banco, não aparece |

Na Suíça e em Luxemburgo o mercado é pequeno e os mesmos recrutadores veem muitas vagas;
**5 candidaturas excelentes por dia valem mais que 30 medianas.**

**Calibração** (depois de ~2 semanas): marque vagas com `python -m jobhunter feedback <job_id> <eu|esposa> good|bad`
e veja onde as "boas" se concentram:

```sql
-- sqlite3 data/jobhunter.db
SELECT candidate, feedback, ROUND(AVG(score_potential)) AS media, MIN(score_potential), COUNT(*)
FROM matches WHERE feedback IS NOT NULL GROUP BY candidate, feedback;
```
Se as "good" estão em 65+ e as "bad" abaixo de 60, ajuste `apply_min_potential` para ~66.

---

## 4. Definindo o que vocês procuram (o passo mais importante)

Para cada pessoa, copie `config/candidates/_exemplo/` para `config/candidates/eu/` e
`config/candidates/esposa/` e preencha três arquivos:

1. **`master_cv.md` — perfil mestre.** *Tudo* o que é verdade: todas as experiências (inclusive as
   que vocês tiraram do CV), números, ferramentas, cursos, idiomas com certificado, voluntariado.
   Sem limite de tamanho. É a única fonte que o sistema pode usar — quanto mais rico, maior o match
   potencial e **nada fora dele aparece num CV gerado**.
2. **`current_cv.md` — o CV de hoje**, convertido para texto (`pip install markitdown && markitdown cv.pdf`).
3. **`profile.yaml` — o que procura**: cargos-alvo e aceitáveis, setores, salário mínimo, taxa de
   ocupação, idiomas reais (CECR), permis, e principalmente os **dealbreakers**.

Exercício que ajuda a preencher o `profile.yaml` (15 min cada um):
- Liste as 3 últimas vagas às quais aplicou. O que as unia? O que vocês aceitaram por falta de opção?
- Se recebessem 2 ofertas no mesmo salário, que critério desempataria? (setor, remoto, tamanho da empresa...)
- O que fariam vocês recusarem uma oferta mesmo com salário bom? → `dealbreakers`.

Depois rode `python -m jobhunter terms` e revise os termos gerados — é o melhor teste de que o
perfil está bem descrito.

---

## 5. CV e carta adaptados (sem inventar nada)

Para as melhores vagas `apply` (máx. 5 por pessoa por dia, configurável):
1. **Adaptação** (`tailor.py`): o Claude reescreve o CV **no idioma da vaga** (FR/EN/DE), espelha
   o vocabulário do anúncio quando corresponde a algo real, reordena experiências e escreve a
   *lettre de motivation* no formato suíço (vous → moi → nous) ou luxemburguês.
   Convenções embutidas: CV de 2 páginas, nacionalidade/permis no cabeçalho, idiomas em CECR,
   taxa de ocupação, datas MM.AAAA.
2. **Auditoria** (`verify`): uma segunda chamada, independente, procura no CV gerado qualquer
   fato sem respaldo no perfil mestre. Se achar, a candidatura fica `needs_review` e **nunca é
   enviada automaticamente**.
3. Arquivos em `data/applications/<pessoa>/<id>-<empresa>-<cargo>/`: `CV-*.pdf`, `Lettre-*.pdf`,
   as versões `.md`/`.html` e `audit.json` (o que mudou e por quê). Tudo também vai para a tabela
   `applications` do banco.

---

## 6. Por que a candidatura não é 100% automática (ainda)

O sistema faz sozinho tudo **até** o botão "enviar". O envio tem três modos:

| Modo | Como funciona | Risco |
|---|---|---|
| `queue` (padrão) | `python -m jobhunter approve` abre CV, carta e link da vaga; você envia e marca. ~2 min por vaga. | nenhum |
| `email` | Envia sozinho **só** quando o anúncio publica um e-mail **e** a auditoria passou. Comece com `dry_run: true`. | baixo |
| robô de portal/LinkedIn | não incluído | alto |

Motivos para não automatizar o portal agora:
- **LinkedIn** proíbe bots no contrato de usuário e bloqueia contas. Perder o LinkedIn no meio de
  uma busca de emprego é muito pior que gastar 2 min por candidatura.
- Na Suíça, a maioria das grandes empresas usa **SuccessFactors, Workday, Taleo, Abacus Umantis**:
  exigem criar conta por empresa, têm captcha e perguntas específicas (permis, pretensão salarial,
  data de disponibilidade) que é arriscado responder às cegas.
- Mercado pequeno: candidatura automática errada (vaga em alemão, pretensão fora da faixa) queima
  o contato com o recrutador para as próximas.

**Fase 2 possível** (depois de 2–3 semanas de confiança nas notas): um assistente com Playwright
que preenche o formulário do ATS e **para antes de enviar** para você conferir — no estilo do
ApplyPilot. Ou plugar o GodsScion só para Easy Apply, com limite de ~5/dia, ciente do risco.

---

## 7. Instalação e uso

```bash
git clone https://github.com/CabralFM/JobHunter.git && cd JobHunter
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium                             # para gerar PDF
cp config/settings.example.yaml config/settings.yaml
cp .env.example .env                                    # coloque a ANTHROPIC_API_KEY
cp -r config/candidates/_exemplo config/candidates/eu
cp -r config/candidates/_exemplo config/candidates/esposa
# ... preencha os 3 arquivos de cada um (§4) ...

set -a; source .env; set +a
python -m jobhunter terms                               # revise os termos de busca
python -m jobhunter probe jobup.ch "chef de projet" Lausanne   # confirma que a fonte funciona
python -m jobhunter probe jobspy "chef de projet" Lausanne
python -m jobhunter run                                 # primeira execução completa
open data/reports/$(date +%F).html                      # relatório do dia
python -m jobhunter approve                             # revisa e envia a fila
```

**Agendar todo dia:**
- Linux/macOS: `scripts/install_cron.sh 07:15` (no Mac, programe o despertar: o script mostra o comando `pmset`).
- Windows: instruções no topo de `scripts/run_daily.ps1` (Agendador de Tarefas com "acordar o computador").

**Testes** (sem rede e sem gastar API): `python -m pytest -q`

**Banco de dados:** `data/jobhunter.db` — abra com [DB Browser for SQLite](https://sqlitebrowser.org/)
ou `sqlite3`. Tabelas: `jobs` (vagas deduplicadas entre sites), `matches` (notas e análise),
`applications` (CVs/cartas gerados e status), `runs` (estatísticas de cada execução).

---

## 8. Custos estimados

Estimativa com Claude Opus 5.5 em todas as etapas (US$ 4 / 20 por milhão de tokens de entrada/saída),
supondo ~150 vagas novas por dia após deduplicação, 2 candidatos. São ordens de grandeza; o
número real aparece no painel da Anthropic depois da primeira semana.

| Etapa | Chamadas/dia | ~US$/dia |
|---|---|---|
| Triagem (1 por vaga, 2 candidatos juntos) | ~120 | ~2,5 |
| Avaliação detalhada | ~60 | ~3,5 |
| CV + carta + auditoria | ~10 | ~2 |
| **Total** | | **~US$ 8/dia (~250/mês)** |

Alavancas, na ordem: (1) o perfil e o CV vão em *prompt caching* — já ativo, cobra ~5% dessa parte
a partir da 2ª chamada; (2) `limits.max_*_per_day` no settings põe um teto rígido; (3) trocar a
**triagem** para `claude-haiku-5-5` reduz essa etapa para centavos — recomendo depois de validar
que ela não está descartando vagas boas; (4) futuro: Batch API (−50%, a execução diária não precisa
de resposta imediata). JobSpy é grátis; atores Apify custam centavos por dia.

---

## 9. Próximos passos sugeridos

1. Preencher os perfis (§4) — sem isso nada funciona bem.
2. `probe` em cada fonte; ajustar jobs.ch/jobup.ch se necessário; escolher os atores Apify para Luxemburgo.
3. 1 semana em modo `queue`, marcando `feedback` → calibrar limiares e pesos.
4. Ligar `email` com `dry_run: true`, depois `false`.
5. Opcional: notificação diária (Telegram/e-mail) com o link do relatório; painel web (Datasette);
   assistente de formulário ATS (§6, fase 2); rastreamento de respostas (entrevista/recusa) para
   aprender quais tipos de vaga convertem.
