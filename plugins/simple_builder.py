import logging
import os
import re
import envoy
from config import config
from bot import send_msg


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def on_push(msg):
    repository_name = msg.get('repository').get('full_name')
    user = msg.get('pusher').get('name')
    commit_id = msg.get('head_commit').get('id')
    short_commit_id = commit_id[0:7]

    repository_config = config.get('builder_repos').get(repository_name)
    build_dir = repository_config.get('build_dir')
    channel = repository_config.get('channel')

    if not os.path.exists(build_dir):
        response = "[{}] Build directory '{}' not found".format(repository_name, build_dir)
        logger.error(response)
        return response

    logger.info('Running `git fetch` in {}'.format(build_dir))
    r = envoy.run('git fetch origin', cwd=build_dir)
    if r.status_code is not 0:
        response = '[{}] `git fetch` returned a non-zero error code ({})'.format(repository_name, r.status_code)
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        return response

    logger.info('Running `git checkout` in {}'.format(build_dir))
    r = envoy.run('git checkout {}'.format(commit_id), cwd=build_dir)
    if r.status_code is not 0:
        response = '[{}] `git checkout` returned a non-zero error code ({})'.format(repository_name, r.status_code)
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        return response

    logger.info('Verifying head commit')
    r = envoy.run('git rev-parse HEAD', cwd=build_dir)
    if r.std_out.strip() != commit_id:
        response = '[{} ({})] `git pull` did not fetch the head commit'.format(repository_name, short_commit_id)
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        return response

    logger.info('Building image in {}'.format(build_dir))
    r = envoy.run('docker build {}'.format(build_dir))
    if r.status_code is not 0:
        build_stderr = [line for line in r.std_err.split('\n') if line]
        response = '[{} ({})] `docker build` failed: {}'.format(repository_name, short_commit_id, build_stderr[-1])
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        logger.debug(build_stderr)
        return response

    build_output = [line for line in r.std_out.split('\n') if line]

    m = re.match('Successfully built ([0-9a-f]+)', build_output[-1])
    if not m:
        response = '`docker build` success message not found in build output'
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        logger.debug(build_output)
        return response

    image_id = m.group(1)
    short_image_id = image_id[0:10]

    logger.info('Running image')
    r = envoy.run('docker run -t --rm {}'.format(image_id))
    if r.status_code is not 0:
        response = '[{} ({})] `docker run` on image {} returned a non-zero error code ({})'.format(repository_name, short_commit_id, short_image_id, r.status_code)
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        return response

    response = '[{} ({})] `docker build` and `docker run` successful'.format(repository_name, short_commit_id)
    logger.info(response)
    return response


def on_message(msg, server):
    logger.debug(msg)

    if msg.get('repository') and msg.get('pusher'):
        repository_name = msg.get('repository').get('full_name')
        if repository_name not in config.get('builder_repos'):
            return

        if msg.get('head_commit') is None:
            return

        repository_config = config.get('builder_repos').get(repository_name)
        channel = repository_config.get('channel')
        short_commit_id = msg.get('head_commit').get('id')[0:7]

        send_msg(channel, '[{} ({})] Starting build'.format(repository_name, short_commit_id))

        response = on_push(msg)

        logger.info('SEND {} {}'.format(channel, response))
        send_msg(channel, response)
    else:
        return
