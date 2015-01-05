import logging
import os
import re
import tempfile
import envoy
from bot import send_msg


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# amount of time to wait for `docker run` to complete
DOCKER_RUN_TIMEOUT = 1800

# amount of time to wait for `docker run` to respond to SIGTERM before
# killing the process
DOCKER_RUN_KILL_TIMEOUT = 30


def on_push(msg):
    from config import config

    repository_name = msg.get('repository').get('full_name')
    user = msg.get('pusher').get('name')
    commit_id = msg.get('head_commit').get('id')
    short_commit_id = commit_id[0:7]

    repository_config = config.get('builder_repos').get(repository_name)
    build_dir = repository_config.get('build_dir')
    channel = repository_config.get('channel')

    # mask out all world permissions
    os.umask(0o007)

    if not os.path.exists(build_dir):
        logger.info('Cloning repository to {}'.format(build_dir))
        hostname = config.get('builder_hostname', 'github.com')
        r = envoy.run('git clone git+ssh://{}/{} {}'.format(hostname, repository_name, build_dir))
        if r.status_code is not 0:
            response = "[{}] `git clone` to `{}` failed".format(repository_name, build_dir)
            logger.error(response)
            logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
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
        response = '[{} ({})] `git checkout` failed to check out the correct commit'.format(repository_name, short_commit_id)
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

    logger.info('Running image %s', image_id)

    cidfile = tempfile.mktemp()
    r = envoy.run('docker run -t --cidfile="{}" {}'.format(cidfile, image_id), timeout=DOCKER_RUN_TIMEOUT, kill_timeout=DOCKER_RUN_KILL_TIMEOUT)
    with open(cidfile) as cidfile_fd:
        container_id = cidfile_fd.read()
    os.unlink(cidfile)

    short_container_id = container_id[0:10]

    if r.status_code is not 0:
        response = '[{} ({})] `docker run` on image {} (in container {}) returned a non-zero error code ({})'.format(repository_name, short_commit_id, short_image_id, short_container_id, r.status_code)
        logger.error(response)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)
        return response

    logger.info('`docker run` was successful, removing container %s', short_container_id)
    r = envoy.run('docker rm {}'.format(container_id))
    if r.status_code is not 0:
        logger.warning('Error removing container %s', short_container_id)
        logger.debug('stdout:\n%s\nstderr:\n%s\n', r.std_out, r.std_err)

    response = '[{} ({})] `docker build` and `docker run` successful'.format(repository_name, short_commit_id)
    logger.info(response)
    return response


def on_message(msg, server):
    logger.debug(msg)

    if msg.get('repository') and msg.get('pusher'):
        repository_name = msg.get('repository').get('full_name')

        logger.info('Reloading configuration')
        import config
        reload(config)
        from config import config

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
