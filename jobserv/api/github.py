# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import hmac
import json
import logging
import time
import traceback
import yaml

import requests

from flask import Blueprint, request, url_for

from jobserv.jsend import ApiError, get_or_404, jsendify
from jobserv.models import Project, ProjectTrigger, TriggerTypes
from jobserv.settings import RUN_URL_FMT
from jobserv.trigger import trigger_build

blueprint = Blueprint('api_github', __name__, url_prefix='/github')


@blueprint.errorhandler(ApiError)
def api_error(e):
    return e.resp


def _get_params(owner, repo, pr_num, token):
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'token ' + token,
    }
    url = 'https://api.github.com/repos/%s/%s/pulls/%d' % (owner, repo, pr_num)
    for x in range(5):
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            try:
                data = r.json()
                return {
                    'GH_PRNUM': int(pr_num),
                    'GH_OWNER': owner,
                    'GH_REPO': repo,
                    'GH_STATUS_URL': data['statuses_url'],
                    'GH_TARGET_REPO': data['base']['repo']['clone_url'],
                    'GIT_URL': data['head']['repo']['clone_url'],
                    'GIT_SHA_BASE': data['base']['sha'],
                    'GIT_SHA': data['head']['sha'],
                }
            except Exception:
                logging.error('Error finding SHA: %d - %s',
                              r.status_code, r.text)
        time.sleep(0.2)
    raise ApiError(500, 'Error finding SHA: %d - %s' % (r.status_code, r.text))


def _get_proj_def(trigger, owner, repo, sha, token):
    if trigger.definition_repo:
        # look up defintion out-of-tree
        name = trigger.definition_file
        if not name:
            name = trigger.project.name + '.yml'
        url = 'https://raw.githubusercontent.com/%s/master/%s' % (
            trigger.definition_repo, name)
    else:
        # look up defintion in tree
        url = 'https://raw.githubusercontent.com/%s/%s/%s/%s' % (
            owner, repo, sha, '.jobserv.yml')

    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'token ' + token,
    }
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        data = yaml.load(resp.text)
        for trigger in data.get('triggers', []):
            if trigger['type'] == 'github_pr':
                return trigger['name'], data
        raise ValueError('No github_pr trigger types defined')
    raise ValueError('Project definition does not exist: ' + url)


def _fail_pr(repo, pr_num, sha, failure_url, token):
    url = 'https://api.github.com/repos/%s/statuses/%s' % (repo, sha)

    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'token ' + token,
    }
    data = {
        'context': 'JobServ',
        'description': 'unexpected failure',
        'state': 'failure',
        'target_url': failure_url,
    }
    return requests.post(url, json=data, headers=headers)


def _update_pr(build, status_url, token):
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'token ' + token,
    }

    for run in build.runs:
        if RUN_URL_FMT:
            url = RUN_URL_FMT.format(project=build.project.name,
                                     build=build.build_id,
                                     run=run.name)
        else:
            url = url_for('api_run.run_get', proj=build.project.name,
                          build_id=build.build_id, run=run.name,
                          _external=True)
        data = {
            'context': run.name,
            'description': 'Build %d' % build.build_id,
            'target_url': url,
            'state': 'pending',
        }
        requests.post(status_url, json=data, headers=headers)


def _validate_payload(trigger):
    secrets = json.loads(trigger.secrets)
    key = secrets.get('webhook-key')
    if not key:
        raise ApiError(403, 'Trigger has no webhook-key secret defined')

    computed = hmac.new(key.encode(), request.data, 'sha1').hexdigest()
    delivered = request.headers.get('X_HUB_SIGNATURE')
    if not delivered or not delivered.startswith('sha1='):
        raise ApiError(404, 'Missing or invalid X_HUB_SIGNATURE header')
    if not (hmac.compare_digest(computed, delivered[5:])):
        raise ApiError(403, 'Invalid X_HUB_SIGNATURE')


def _filter_events(event):
    ignores = ('fork', 'ping', 'push', 'status', 'pull_request_review',
               'pull_request_review_comment')
    events = ignores + ('issue_comment', 'pull_request')
    if event not in events:
        raise ApiError(400, 'Invalid action: ' + event)
    if event in ignores:
        raise ApiError(200, 'OK, ignoring')


@blueprint.route('/<project:proj>/', methods=('POST',))
def on_webhook(proj):
    trigger = get_or_404(ProjectTrigger.query.filter(
        ProjectTrigger.type == TriggerTypes.github_pr.value
    ).join(
        Project
    ).filter(
        Project.name == proj
    ))
    _validate_payload(trigger)
    event = request.headers.get('X-Github-Event')
    _filter_events(event)

    data = request.get_json()
    if event == 'issue_comment':
        if 'ci-retest' not in request.json['comment']['body']:
            return 'Ingoring comment'
        pr_num = data['issue']['number']
        repo = data['repository']['full_name']
    elif event == 'pull_request':
        if data['action'] not in ('opened', 'synchronize'):
            return 'Ignoring action: ' + request.json['action']
        pr_num = data['pull_request']['number']
        repo = data['pull_request']['base']['repo']['full_name']

    reason = 'GitHub PR(%s): %s' % (pr_num, event)
    secrets = json.loads(trigger.secrets)
    token = secrets['githubtok']
    owner, repo = repo.split('/')
    params = _get_params(owner, repo, pr_num, token)
    try:
        trig, proj = _get_proj_def(
            trigger, owner, repo, params['GIT_SHA'], token)
        b = trigger_build(trigger.project, reason, trig, params, secrets, proj)
        _update_pr(b, params['GH_STATUS_URL'], token)
        url = url_for('api_build.build_get',
                      proj=trigger.project.name, build_id=b.build_id,
                      _external=True)
        return jsendify({'url': url}, 201)
    except ApiError as e:
        url = e.resp.headers.get('Location')
        _fail_pr(repo, pr_num, params['GIT_SHA'], url, token)
        raise
    except Exception as e:
        _fail_pr(repo, pr_num, params['GIT_SHA'], None, token)
        tb = traceback.format_exc()
        return 'FAILED: %s: %s\n%s' % (repo, pr_num, tb), 500
