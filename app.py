"""EstágioJá - rotas Flask. A lógica de busca fica em services/job_service.py."""

import logging
import os
from datetime import datetime

from flask import Flask, Response, render_template, request, url_for

from services import job_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("estagioja")

app = Flask(__name__)


def _criteria_from_request() -> job_service.Criteria:
    """Lê e valida os filtros; DEMO_MODE=1 no ambiente força o modo demonstração."""
    criteria = job_service.parse_criteria(request.args)
    if os.environ.get("DEMO_MODE") == "1":
        criteria.demo = True
    return criteria


@app.route("/")
def index():
    criteria = _criteria_from_request()
    searched = "buscar" in request.args
    result = job_service.search_jobs(criteria) if searched else None
    if result:
        logger.info("Busca: %s -> %d vagas", criteria, len(result.jobs))
    return render_template(
        "index.html",
        criteria=criteria,
        searched=searched,
        result=result,
        area_options=job_service.AREA_OPTIONS,
        mode_options=job_service.MODE_OPTIONS,
        mode_labels=job_service.MODE_LABELS,
        export_url=url_for("exportar", **criteria.to_params()),
        external_links=job_service.external_search_links(criteria),
    )


@app.route("/exportar")
def exportar():
    """Repete a busca com os mesmos filtros e devolve o CSV para download."""
    result = job_service.search_jobs(_criteria_from_request())
    filename = f"estagioja_{datetime.now():%Y%m%d_%H%M}.csv"
    logger.info("Exportação CSV: %d vagas", len(result.jobs))
    return Response(
        "\ufeff" + job_service.jobs_to_csv(result.jobs),  # BOM: acentos corretos no Excel
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
