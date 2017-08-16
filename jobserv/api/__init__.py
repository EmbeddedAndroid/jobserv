from jobserv.api.build import blueprint as build_bp
from jobserv.api.github import blueprint as github_bp
from jobserv.api.gitlab import blueprint as gitlab_bp
from jobserv.api.health import blueprint as health_bp
from jobserv.api.project import blueprint as project_bp
from jobserv.api.project_triggers import blueprint as project_triggers_bp
from jobserv.api.run import blueprint as run_bp
from jobserv.api.test import blueprint as test_bp
from jobserv.api.test_query import blueprint as test_query_bp
from jobserv.api.worker import blueprint as worker_bp
from jobserv.jsend import ApiError

BLUEPRINTS = (
    project_bp, project_triggers_bp, build_bp, run_bp, test_bp, test_query_bp,
    worker_bp, health_bp, github_bp, gitlab_bp,
)


def register_blueprints(app):
    for bp in BLUEPRINTS:
        @bp.errorhandler(ApiError)
        def api_error(e):
            return e.resp
        app.register_blueprint(bp)