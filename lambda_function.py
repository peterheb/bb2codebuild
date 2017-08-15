# github.com/peterheb/bb2codebuild - Copyright (c) 2017 Peter Hebert
# Licensed under the MIT License
#
# AWS Lambda function for invoking AWS CodeBuild in response to a Bitbucket push
# webhook. Useful if you don't want to switch to CodeCommit and don't need a
# CodePipeline.
#
# To set up manually in the AWS Console:
#
#  - Use the Python 2.7 lambda runtime
#  - Connect to API Gateway with 'Use Lambda Proxy integration' turned on (this
#    is the default)
#  - Ensure this Lambda function's IAM role includes CodeBuild StartBuild and
#    BatchGetProjects for *, and AWSLambdaBasicExecutionRole for logging.
#
# pylint: disable-msg=C0103,R0912
"""Bitbucket Cloud webhook processor for CodeBuild integration"""

import json
import logging
import os
import re
from string import Template
from urlparse import urlparse
import boto3

# set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)

# AWS clients
cb_client = boto3.client('codebuild')

def webhook(event, context):
    """Process a Bitbucket webhook. (Lambda entry point)"""
    # CodeBuild project name pattern
    pattern = os.environ.get('pattern', '$username-$reponame-$branch')
    if ('$username' not in pattern) or ('$reponame' not in pattern) or ('$branch' not in pattern):
        raise RuntimeError('pattern env. variable must contain $username, $reponame, $branch')

    # if a token is configured, make sure it is specified on the query string
    query_string = event['queryStringParameters']
    token_in = query_string.get('token', '') if query_string else ''
    if os.environ.get('token', '') != token_in:
        logger.error('Token received "%s" does not match configured value', token_in)
        return {
            'statusCode':403,
            'headers':{'Content-Type':'text/plain'},
            'body':'403 Forbidden'
        }

    # do some basic validation on the webhook request
    if not event['headers'].get('User-Agent', '').startswith('Bitbucket-Webhooks/'):
        raise RuntimeError('User-Agent is "%s", not "Bitbucket-Webhooks/*"' %
                           event['headers'].get('User-Agent', ''))

    if event['headers'].get('X-Event-Key', '') != 'repo:push':
        raise RuntimeError('X-Event-Key is "%s", not "repo:push"' %
                           event['headers'].get('X-Event-Key', ''))

    # load the body and start processing it
    data = json.loads(event['body'])
    if ('push' not in data) or ('repository' not in data):
        raise RuntimeError('Webhook request body is missing essential keys')

    repo_owner = data['repository']['owner']['username']
    repo_name = data['repository']['name']
    logger.info('Received webhook notification for %s/%s:', repo_owner, repo_name)

    # ['type'] can be 'branch' or 'tag'; within a change, ['new'], ['old'], or both may exist.
    # store new pushes in changeset, adding ['linked_proj'] = desired CodeBuild project name
    changeset = []
    for change in data['push']['changes']:
        if change['new']:
            logger.info('--%s %s to %s%s', change['new']['type'], change['new']['name'],
                        change['new']['target']['hash'], ' (created)' if change['created'] else '')
            # desired CodeBuild project name based on branch name, or 'all_tags' if this is a tag
            change['linked_proj'] = clean_name(Template(pattern).substitute(
                username=repo_owner, reponame=repo_name,
                branch=('all_tags' if change['new']['type'] == 'tag' else change['new']['name'])))
            changeset.append(change)
        elif change['closed']:
            logger.info('--%s %s deleted', change['old']['type'], change['old']['name'])

    # query CodeBuild to see if any build projects exist for this repo+branch combo(s)
    # Bitbucket will send multiple changes at once after a 'git push --all', so it is
    # possible that we could trigger multiple builds.
    project_names = [c['linked_proj'] for c in changeset]
    if changeset:
        cb_resp = cb_client.batch_get_projects(names=project_names)

    # exit with 200/OK if there are no builds to start
    if (not changeset) or (not cb_resp['projects']):
        if not changeset:
            logger.info('No builds to create')
        else:
            logger.info('No CodeBuild projects exist for these changes: %s', project_names)
        return {
            'statusCode':200,
            'headers':{'Content-Type':'application/json'},
            'body':json.dumps({'action':'no-build', 'lambdaId':context.aws_request_id,
                               'buildId':[]})
        }

    # start builds
    build_ids = []
    for project in cb_resp['projects']:
        logger.info('Starting CodeBuild project %s:', project['name'])
        change = [c for c in changeset if c['linked_proj'] == project['name']]
        build_ids.append(start_build(change[0], project))

    # done - return 200 OK message to Bitbucket
    return {
        'statusCode':200,
        'headers':{'Content-Type':'application/json'},
        'body':json.dumps({'action':'build', 'lambdaId':context.aws_request_id,
                           'buildId':build_ids})
    }

def clean_name(name):
    """Substitute characters from name that are not valid in CodeBuild project names."""
    return re.sub(r'[^-_A-Za-z0-9]', '_', name)

def start_build(change, project):
    """Start a CodeBuild build job."""
    # figure out which branch/tag we're building
    branch_name = change['new']['name']

    # make sure this CodeBuild project is Bitbucket-linked
    source_loc = project['source']['location']
    if (not project['source']['location'].startswith('https://')) or (
            urlparse(source_loc).hostname != 'bitbucket.org'):
        raise RuntimeError('CodeBuild project is not Bitbucket-backed, src = ' + source_loc)

    # set up our codebuild start_build args
    start_args = {
        'projectName':project['name'],
        'sourceVersion':change['new']['target']['hash'],
        'environmentVariablesOverride':[
            {'name':'GIT_COMMIT', 'value':change['new']['target']['hash']},
            {'name':'GIT_BRANCH', 'value':branch_name}
        ]
    }

    # provide artifactsOverride if we are building to S3 and the artifact name contains '(tag)'
    if (project['artifacts']['type'] == 'S3') and ('(tag)' in project['artifacts'].get('name', '')):
        start_args['artifactsOverride'] = {
            'type':'S3',
            'location':project['artifacts']['location'],
            'namespaceType':project['artifacts'].get('namespaceType', 'NONE'),
            'name':project['artifacts']['name'].replace('(tag)', branch_name),
            'packaging':project['artifacts'].get('packaging', 'NONE')
        }

        # normally path is omitted; we carry it through only if it is defined
        if 'path' in project['artifacts']:
            start_args['artifactsOverride']['path'] = project['artifacts']['path']

    # start the build job
    resp = cb_client.start_build(**start_args)
    build_id = resp['build']['id']
    logger.info('--Build ID = %s', build_id)

    # return the build ID
    return build_id
