from __future__ import annotations

import csv
import html
import io
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

#Configurações
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
SAMPLE_FILE = BASE_DIR / "data" / "sample_jobs.json"

ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"

REQUEST_TIMEOUT = (5, 15)
USER_AGENT = "EstagioJa/1.0 (projeto academico; Python requests)"

GITHUB_ISSUES_URL = "https://api.github.com/repos/{repo}/issues"
# Repositórios de vagas das comunidades brasileiras (repositório, tema)
GITHUB_REPOS = [
    ("backend-br/vagas", "Back-end"),
    ("pydevbr/vagas", "Python"),
    ("frontendbr/vagas", "Front-end"),
    ("nodejsdevbr/vagas", "Node.js"),
    ("soujava/vagas-java", "Java"),
    ("dotnetdevbr/vagas", ".NET"),
]
GITHUB_ISSUES_PER_REPO = 100
GITHUB_CACHE_TTL = 30 * 60         # 30 minutos (6 repositórios = 6 requisições)

ARBEITNOW_PAGES = 3
ARBEITNOW_CACHE_TTL = 30 * 60      # 30 minutos
REMOTIVE_CACHE_TTL = 6 * 60 * 60   # 6 horas (~4 consultas/dia, limite da Remotive)

SUMMARY_LENGTH = 240
MAX_QUERY_LENGTH = 80
MAX_LOCATION_LENGTH = 60
MAX_TERMS = 5

#Opções de filtro (chave interna, rótulo exibido)
AREA_OPTIONS = [
    ("all", "Todas as áreas de tecnologia"),
    ("python_backend", "Python e back-end"),
    ("data_science", "Data Science e análise de dados"),
    ("data_engineering", "Engenharia de dados e Big Data"),
    ("software", "Desenvolvimento de software em geral"),
]
MODE_OPTIONS = [
    ("all", "Qualquer modalidade"),
    ("remota", "Remota"),
    ("hibrida", "Híbrida"),
    ("presencial", "Presencial"),
]
MODE_LABELS = {"remota": "Remota", "hibrida": "Híbrida", "presencial": "Presencial"}

#Padrões (regex) aplicados sobre texto sem acentos e em minúsculas
AREA_PATTERNS = {
    "python_backend": r"\bpython\b|\bdjango\b|\bflask\b|\bfastapi\b|\bback[- ]?end\b|\bserver[- ]side\b|\bapis?\b",
    "data_science": r"data scien|\bmachine learning\b|\bml\b|analise de dados|data analy|\banalytics\b|ciencia de dados|\bstatistic|\bpandas\b|power ?bi|\bbi\b|\bai\b|artificial intelligence|\bnlp\b",
    "data_engineering": r"data engineer|engenharia de dados|big data|\betl\b|\bspark\b|hadoop|airflow|kafka|data pipeline|data warehouse|\bdbt\b|databricks|data platform",
    "software": r"software|developer|desenvolvedor|entwickl|programm|full[- ]?stack|front[- ]?end|back[- ]?end|\bdevops\b|web development",
}
#Termos de estágio em português e inglês (título e tags apenas)
INTERNSHIP_PATTERN = re.compile(
    r"estagi|\binterns?\b|\binternships?\b|praktik|werkstudent|\btrainee\b"
)


class SourceError(Exception):
    """Falha ao consultar uma fonte externa (mensagem pronta para o usuário)."""


# --------------------------------- Modelos ----------------------------------
@dataclass
class Job:
    """Vaga em formato interno padronizado, independente da fonte."""
    id: str
    title: str
    company: str
    location: str
    work_mode: str  # "remota", "hibrida" ou "presencial"
    summary: str
    source: str
    url: str
    published: datetime | None = None
    tags: list[str] = field(default_factory=list)
    is_demo: bool = False
    search_text: str = field(default="", repr=False)  # texto sem acentos para filtros


@dataclass
class SourceStatus:
    """Resultado da consulta a uma fonte, exibido na interface."""
    name: str
    ok: bool
    count: int = 0
    message: str = ""
    fetched_at: str = ""
    from_cache: bool = False
    credit_url: str = ""


@dataclass
class Criteria:
    """Filtros escolhidos pelo usuário (já validados)."""
    query: str = ""
    area: str = "all"
    work_mode: str = "all"
    location: str = ""
    internship_only: bool = True
    demo: bool = False

    def to_params(self) -> dict[str, str]:
        """Converte para parâmetros de URL (usado no link de exportação)."""
        return {
            "buscar": "1",
            "q": self.query,
            "area": self.area,
            "modalidade": self.work_mode,
            "local": self.location,
            "estagio": "1" if self.internship_only else "0",
            "demo": "1" if self.demo else "0",
        }


@dataclass
class SearchResult:
    jobs: list[Job]
    statuses: list[SourceStatus]
    total_fetched: int
    queried_at: str
    demo: bool

    @property
    def companies_count(self) -> int:
        return len({_fold(j.company) for j in self.jobs})

    @property
    def all_failed(self) -> bool:
        return bool(self.statuses) and not any(s.ok for s in self.statuses)


# ------------------------------ Texto utilitário ----------------------------
def _fold(text: str) -> str:
    """Minúsculas e sem acentos, para comparações tolerantes."""
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _clean_text(raw: str | None) -> str:
    """Remove tags HTML, entidades e espaços repetidos."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _shorten(text: str, limit: int = SUMMARY_LENGTH) -> str:
    """Corta o texto no limite sem partir palavras."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _safe_url(url: str | None) -> str | None:
    """Aceita apenas links http(s); descarta qualquer outro esquema."""
    if isinstance(url, str) and re.match(r"^https?://", url.strip(), re.IGNORECASE):
        return url.strip()
    return None


def _detect_work_mode(is_remote: bool, text: str) -> str:
    if is_remote:
        return "remota"
    if re.search(r"hybrid|hibrid|teilweise remote", _fold(text)):
        return "hibrida"
    return "presencial"


# --------------------------- Requisição HTTP e cache ------------------------
def _get_json(url: str, params: Mapping[str, object] | None = None,
              extra_headers: Mapping[str, str] | None = None, expect: type = dict):
    """GET com timeout, tratamento de erros e validação básica do JSON."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    headers.update(extra_headers or {})
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,  # verificação SSL permanece ativada (padrão)
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout as exc:
        raise SourceError("tempo de resposta esgotado") from exc
    except requests.ConnectionError as exc:
        raise SourceError("sem conexão com o serviço") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        detail = "limite de requisições atingido" if status in (403, 429) else f"HTTP {status}"
        raise SourceError(f"o serviço respondeu com erro ({detail})") from exc
    except ValueError as exc:
        raise SourceError("resposta inválida (JSON malformado)") from exc
    except requests.RequestException as exc:
        raise SourceError("erro inesperado na requisição") from exc

    if not isinstance(data, expect):
        raise SourceError("formato de resposta inesperado")
    return data


def _read_cache(name: str) -> tuple[list[dict], float] | None:
    """Lê o cache salvo em disco. Retorna (itens, timestamp) ou None."""
    try:
        content = json.loads((CACHE_DIR / f"{name}.json").read_text(encoding="utf-8"))
        items, fetched_at = content["items"], float(content["fetched_at"])
        return (items, fetched_at) if isinstance(items, list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(name: str, items: list[dict]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        payload = {"fetched_at": time.time(), "items": items}
        (CACHE_DIR / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.warning("Não foi possível gravar o cache %s: %s", name, exc)


def _load_source(name: str, ttl: int, fetch_raw: Callable[[], list[dict]]):
    """
    Devolve (itens_brutos, timestamp, veio_do_cache, aviso).
    Usa o cache enquanto válido; se a atualização falhar, usa o cache antigo.
    """
    cached = _read_cache(name)
    if cached and time.time() - cached[1] < ttl:
        return cached[0], cached[1], True, ""
    try:
        items = fetch_raw()
    except SourceError as exc:
        if cached:
            logger.warning("%s: falha (%s); usando cache antigo", name, exc)
            return cached[0], cached[1], True, f"Falha ao atualizar ({exc}); exibindo dados salvos."
        raise
    _write_cache(name, items)
    return items, time.time(), False, ""


# ------------------------------- Fonte: GitHub Issues -----------------------
_TITLE_LOCATION = re.compile(r"^\s*\[([^\]]{1,60})\]\s*")
# Empresa só é extraída em padrões seguros: "... na Empresa", "... at Empresa", "... @ Empresa"
_TITLE_COMPANY = re.compile(r"(?:\s@\s*|\s(?:na|at)\s+)([A-Z0-9][^\[\]|]{1,50})$")


def _fetch_github_raw() -> list[dict]:
    """Lê as issues abertas de cada repositório de vagas (PRs são ignorados)."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")  # opcional; nunca fica no código
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list[dict] = []
    first_error: SourceError | None = None
    consecutive_failures = 0
    for repo, topic in GITHUB_REPOS:
        if consecutive_failures >= 2:  # provável queda de rede ou limite atingido: desiste
            break
        params = {"state": "open", "sort": "created", "direction": "desc",
                  "per_page": GITHUB_ISSUES_PER_REPO}
        try:
            issues = _get_json(GITHUB_ISSUES_URL.format(repo=repo), params, headers, expect=list)
        except SourceError as exc:
            logger.warning("GitHub %s: %s", repo, exc)
            first_error = first_error or exc
            consecutive_failures += 1
            continue
        consecutive_failures = 0
        for issue in issues:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            items.append({
                "repo": repo,
                "topic": topic,
                "number": issue.get("number"),
                "title": issue.get("title"),
                "url": issue.get("html_url"),
                "body": (issue.get("body") or "")[:3000],
                "labels": [l.get("name") for l in issue.get("labels", []) if isinstance(l, dict)],
                "created_at": issue.get("created_at"),
                "author": (issue.get("user") or {}).get("login"),
            })
        time.sleep(0.3)
    if not items:
        raise first_error or SourceError("a fonte devolveu uma lista vazia")
    return items


def _clean_markdown(text: str) -> str:
    """Remove a sintaxe Markdown mais comum do corpo das issues."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links e imagens
    text = re.sub(r"[#*_`>|~]+", " ", text)
    return _clean_text(text)


def _keyword_mode(text: str) -> str | None:
    """Procura palavras de modalidade num texto; None se não encontrar."""
    folded = _fold(text)
    if re.search(r"hibrid|hybrid", folded):
        return "hibrida"
    if re.search(r"remot|home ?office|anywhere", folded):
        return "remota"
    return None


def _normalize_github(raw: dict) -> Job | None:
    full_title = _clean_text(raw.get("title"))
    url = _safe_url(raw.get("url"))
    if not (full_title and url):
        return None

    # Padrão das comunidades: "[Cidade] Cargo na Empresa"
    bracket = _TITLE_LOCATION.match(full_title)
    location_hint = _clean_text(bracket.group(1)) if bracket else ""
    title = full_title[bracket.end():] if bracket else full_title
    title = title or full_title
    company_match = _TITLE_COMPANY.search(title)
    author = raw.get("author")
    company = (
        _clean_text(company_match.group(1)) if company_match
        else (f"Publicada por @{author}" if author else "Empresa não informada")
    )

    labels = [str(l) for l in (raw.get("labels") or []) if l]
    body = _clean_markdown(raw.get("body") or "")
    # Modalidade: título e labels têm prioridade; depois o início do corpo
    header_text = f"{full_title} {' '.join(labels)}"
    mode = _keyword_mode(header_text) or _keyword_mode(body[:600]) or "presencial"

    published = None
    created = raw.get("created_at")
    if isinstance(created, str):
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            pass

    topic = raw.get("topic") or ""
    tags = [t for t in [topic, "Software"] + labels if t]
    return Job(
        id=f"github:{raw.get('repo')}#{raw.get('number') or url}",
        title=title,
        company=company,
        # Os repositórios são de comunidades brasileiras; a cidade vem do título da issue
        location=f"{location_hint} - Brasil" if location_hint else "Brasil",
        work_mode=mode,
        summary=_shorten(body) or "Sem descrição disponível. Veja os detalhes na issue original.",
        source=f"GitHub ({raw.get('repo')})",
        url=url,
        published=published,
        tags=tags,
        search_text=_fold(f"{full_title} {' '.join(tags)} {body}"),
    )


# ------------------------------- Fonte: Arbeitnow ---------------------------
def _fetch_arbeitnow_raw() -> list[dict]:
    items: list[dict] = []
    for page in range(1, ARBEITNOW_PAGES + 1):
        try:
            data = _get_json(ARBEITNOW_URL, {"page": page})
        except SourceError:
            if page == 1:
                raise
            logger.warning("Arbeitnow: falha na página %s; usando as anteriores", page)
            break
        page_items = data.get("data")
        if not isinstance(page_items, list):
            raise SourceError("campo 'data' ausente na resposta")
        if not page_items:
            break
        items.extend(i for i in page_items if isinstance(i, dict))
        time.sleep(0.5)  # educação com o servidor
    if not items:
        raise SourceError("a fonte devolveu uma lista vazia")
    return items


def _normalize_arbeitnow(raw: dict) -> Job | None:
    title, company = _clean_text(raw.get("title")), _clean_text(raw.get("company_name"))
    url = _safe_url(raw.get("url"))
    if not (title and company and url):
        return None

    location = _clean_text(raw.get("location")) or "Não informado"
    tags = [str(t) for t in (raw.get("tags") or []) + (raw.get("job_types") or [])]
    description = _clean_text(raw.get("description"))
    created = raw.get("created_at")
    published = (
        datetime.fromtimestamp(created, timezone.utc) if isinstance(created, (int, float)) else None
    )
    return Job(
        id=f"arbeitnow:{raw.get('slug') or url}",
        title=title,
        company=company,
        location=location,
        work_mode=_detect_work_mode(bool(raw.get("remote")), f"{title} {location}"),
        summary=_shorten(description) or "Sem descrição disponível.",
        source="Arbeitnow",
        url=url,
        published=published,
        tags=tags,
        search_text=_fold(f"{title} {' '.join(tags)} {description[:4000]}"),
    )


# ------------------------------- Fonte: Remotive ----------------------------
def _fetch_remotive_raw() -> list[dict]:
    data = _get_json(REMOTIVE_URL)  # uma única consulta; o cache respeita o limite diário
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        raise SourceError("campo 'jobs' ausente na resposta")
    if not jobs:
        raise SourceError("a fonte devolveu uma lista vazia")
    keep = ("id", "url", "title", "company_name", "category", "tags", "job_type",
            "publication_date", "candidate_required_location")
    trimmed = []
    for job in jobs:
        if isinstance(job, dict):
            item = {k: job.get(k) for k in keep}
            item["description"] = _clean_text(job.get("description"))[:3000]
            trimmed.append(item)
    return trimmed


def _normalize_remotive(raw: dict) -> Job | None:
    title, company = _clean_text(raw.get("title")), _clean_text(raw.get("company_name"))
    url = _safe_url(raw.get("url"))
    if not (title and company and url):
        return None

    location = _clean_text(raw.get("candidate_required_location")) or "Remoto (local não informado)"
    tags = [str(t) for t in (raw.get("tags") or [])]
    tags += [str(raw[k]) for k in ("category", "job_type") if raw.get(k)]
    description = raw.get("description") or ""
    try:
        published = datetime.fromisoformat(raw["publication_date"]).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        published = None
    return Job(
        id=f"remotive:{raw.get('id') or url}",
        title=title,
        company=company,
        location=location,
        work_mode="remota",  # a Remotive lista apenas vagas remotas
        summary=_shorten(description) or "Sem descrição disponível.",
        source="Remotive",
        url=url,
        published=published,
        tags=tags,
        search_text=_fold(f"{title} {' '.join(tags)} {description}"),
    )


# Fontes reais: (nome, chave de cache, TTL, função de coleta, normalizador, link de crédito)
SOURCES = [
    ("GitHub (vagas BR)", "github", GITHUB_CACHE_TTL, _fetch_github_raw,
     _normalize_github, "https://github.com/frontendbr/vagas"),
    ("Arbeitnow", "arbeitnow", ARBEITNOW_CACHE_TTL, _fetch_arbeitnow_raw,
     _normalize_arbeitnow, "https://www.arbeitnow.com"),
    ("Remotive", "remotive", REMOTIVE_CACHE_TTL, _fetch_remotive_raw,
     _normalize_remotive, "https://remotive.com"),
]


def fetch_all_jobs() -> tuple[list[Job], list[SourceStatus]]:
    """Consulta todas as fontes reais. Uma fonte com erro não derruba as outras."""
    jobs: list[Job] = []
    statuses: list[SourceStatus] = []
    for name, cache_key, ttl, fetch_raw, normalize, credit_url in SOURCES:
        try:
            raw_items, fetched_ts, from_cache, warning = _load_source(cache_key, ttl, fetch_raw)
        except SourceError as exc:
            logger.error("%s indisponível: %s", name, exc)
            statuses.append(SourceStatus(name, ok=False, message=str(exc), credit_url=credit_url))
            continue
        normalized = [j for j in (normalize(r) for r in raw_items) if j]
        jobs.extend(normalized)
        statuses.append(SourceStatus(
            name, ok=True, count=len(normalized), message=warning, from_cache=from_cache,
            fetched_at=datetime.fromtimestamp(fetched_ts).strftime("%d/%m/%Y %H:%M"),
            credit_url=credit_url,
        ))
        logger.info("%s: %d vagas (cache=%s)", name, len(normalized), from_cache)
    return jobs, statuses


#Modo demonstração
def load_demo_jobs() -> list[Job]:
    """Carrega vagas FICTÍCIAS de data/sample_jobs.json (nunca usado na busca real)."""
    data = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    jobs = []
    for item in data:
        tags = item.get("tags", [])
        jobs.append(Job(
            id=f"demo:{item['id']}", title=item["title"], company=item["company"],
            location=item["location"], work_mode=item["work_mode"], summary=item["summary"],
            source="Demonstração (dados fictícios)", url=item["url"],
            published=datetime.fromisoformat(item["published"]).replace(tzinfo=timezone.utc),
            tags=tags, is_demo=True,
            search_text=_fold(f"{item['title']} {' '.join(tags)} {item['summary']}"),
        ))
    return jobs


# ------------------------- Duplicatas, filtros e busca ----------------------
def deduplicate(jobs: list[Job]) -> list[Job]:
    """Remove vagas repetidas (mesma URL ou mesmo título + empresa)."""
    seen_urls: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    unique = []
    for job in jobs:
        key = (_fold(job.title), _fold(job.company))
        if job.url in seen_urls or key in seen_keys:
            continue
        seen_urls.add(job.url)
        seen_keys.add(key)
        unique.append(job)
    return unique


def is_internship(job: Job) -> bool:
    """Vaga de estágio: termos no título ou nas tags (a descrição gera falsos positivos)."""
    return bool(INTERNSHIP_PATTERN.search(_fold(f"{job.title} {' '.join(job.tags)}")))


def _location_matches(job: Job, wanted: str) -> bool:
    location = _fold(job.location)
    open_remote = job.work_mode == "remota" and re.search(r"worldwide|anywhere|global", location)
    return wanted in location or bool(open_remote)


def filter_jobs(jobs: list[Job], criteria: Criteria) -> list[Job]:
    """Aplica área, modalidade, localização, estágio e palavras-chave (todas devem casar)."""
    if criteria.area == "all":
        area_regex = re.compile("|".join(AREA_PATTERNS.values()))
    else:
        area_regex = re.compile(AREA_PATTERNS[criteria.area])
    terms = _fold(criteria.query).split()[:MAX_TERMS]
    wanted_location = _fold(criteria.location).strip()

    result = []
    for job in jobs:
        if criteria.internship_only and not is_internship(job):
            continue
        if not area_regex.search(job.search_text):
            continue
        if criteria.work_mode != "all" and job.work_mode != criteria.work_mode:
            continue
        if wanted_location and not _location_matches(job, wanted_location):
            continue
        if terms and not all(t in job.search_text for t in terms):
            continue
        result.append(job)
    # Mais recentes primeiro; vagas sem data vão para o fim
    result.sort(key=lambda j: j.published.timestamp() if j.published else 0, reverse=True)
    return result


def search_jobs(criteria: Criteria) -> SearchResult:
    """Fluxo completo: coletar, deduplicar e filtrar. Nunca lança exceção de rede."""
    if criteria.demo:
        try:
            jobs = load_demo_jobs()
            statuses = [SourceStatus("Demonstração (dados fictícios)", ok=True, count=len(jobs))]
        except (OSError, ValueError, KeyError) as exc:
            logger.error("Falha ao carregar o modo demonstração: %s", exc)
            jobs = []
            statuses = [SourceStatus("Demonstração", ok=False, message="arquivo de dados inválido")]
    else:
        jobs, statuses = fetch_all_jobs()

    unique = deduplicate(jobs)
    return SearchResult(
        jobs=filter_jobs(unique, criteria),
        statuses=statuses,
        total_fetched=len(unique),
        queried_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
        demo=criteria.demo,
    )


#Atalhos para outras plataformas
#Sites sem API pública: o app NÃO coleta dados deles. Apenas monta o link da busca,
# que o usuário abre no próprio navegador.
EXTERNAL_SEARCHES = [
    ("Indeed", "https://br.indeed.com/jobs?q={q}&l={l}"),
    ("LinkedIn", "https://www.linkedin.com/jobs/search/?keywords={q}&location={l}"),
    ("Gupy", "https://portal.gupy.io/job-search/term={q}"),
    ("InfoJobs", "https://www.infojobs.com.br/empregos.aspx?palabra={q}"),
]


def external_search_links(criteria: Criteria) -> list[tuple[str, str]]:
    """Monta links de busca pré-preenchidos (nome do site, URL) a partir dos filtros."""
    words = (["estágio"] if criteria.internship_only else []) + [criteria.query]
    query = " ".join(w for w in words if w).strip() or "desenvolvedor"
    location = criteria.location or "Brasil"
    return [
        (name, template.format(q=quote(query, safe=""), l=quote(location, safe="")))
        for name, template in EXTERNAL_SEARCHES
    ]


#Validação dos parâmetros
def parse_criteria(args: Mapping[str, str]) -> Criteria:
    """Valida os parâmetros da URL; valores inválidos voltam ao padrão."""
    searched = "buscar" in args
    area = args.get("area", "all")
    mode = args.get("modalidade", "all")
    return Criteria(
        query=args.get("q", "").strip()[:MAX_QUERY_LENGTH],
        area=area if area in dict(AREA_OPTIONS) else "all",
        work_mode=mode if mode in dict(MODE_OPTIONS) else "all",
        location=args.get("local", "").strip()[:MAX_LOCATION_LENGTH],
        # Antes da primeira busca o filtro de estágio começa marcado
        internship_only=(args.get("estagio") == "1") if searched else True,
        demo=args.get("demo") == "1",
    )


#Exportação
def _csv_safe(value: str) -> str:
    """Evita injeção de fórmulas ao abrir o CSV no Excel."""
    return "'" + value if value and value[0] in "=+-@\t\r" else value


def jobs_to_csv(jobs: list[Job]) -> str:
    """Gera o CSV (separador ';', que o Excel em português abre corretamente)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Título", "Empresa", "Localização", "Modalidade", "URL original",
                     "Fonte", "Publicada em"])
    for job in jobs:
        writer.writerow([
            _csv_safe(job.title), _csv_safe(job.company), _csv_safe(job.location),
            MODE_LABELS.get(job.work_mode, job.work_mode), job.url, job.source,
            job.published.strftime("%d/%m/%Y") if job.published else "",
        ])
    return buffer.getvalue()
