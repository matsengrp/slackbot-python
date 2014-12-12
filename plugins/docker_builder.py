"""usage: !docker <command> [<args>]

Available commands:
   build <github repository>       checkout and build an image from github
   build_url <repository url>      checkout and build an image from a git repository
   help                            show this help
   run <image id>                  create and run a container
"""

# structure borrowed from atlassian-jira.py

import envoy
import logging
import os
import re
import shutil
import tempfile

def help():
    return __doc__

def clone_repository(repository_url, build_dir):
    r = envoy.run('git clone {} {}'.format(repository_url, build_dir))
    if r.status_code is not 0:
        raise RuntimeError('git returned a non-zero status code ({}):\n{}\n{}'
                           .format(r.status_code, r.std_out, r.std_err))


def build_from_name(conn, args):
    if len(args) < 1:
        return help()

    repository = args[0]
    repository_url = 'git+ssh://github.com/{}.git'.format(repository)
    return build_from_url(conn, [repository_url])


def build_from_url(conn, args):
    if len(args) < 1:
        return help()

    logger = logging.getLogger(__name__)

    # regex from Kel Solaar, http://stackoverflow.com/a/22312124
    url = args[0]
    m = re.match('((git(\+ssh)?|ssh|http(s)?)|(git@[\w\.]+))(:(//)?)([\w\.@\:/\-~]+)(\.git)(/)?', url)
    if not m:
        return help()

    repository_url = m.group(0)
    build_dir = tempfile.mkdtemp()

    response = None

    try:
        logger.info('Cloning {} to {}'.format(repository_url, build_dir))
        clone_repository(repository_url, build_dir)

        logger.info('Building image')
        build_output = [line for line in conn.build(path=build_dir, rm=True)]

        # envoy-based runner in case of trouble with docker-py
        #r = envoy.run('docker build {}'.format(build_dir))
        #build_output = [line for line in r.std_out.split('\n') if line]

        # check the build output for success
        m = re.search('Successfully built ([0-9a-f]+)', build_output[-1])
        if not m:
            raise RuntimeError('Error building image: {}'.format(build_output[-1]))

        image = m.group(1)
        response = 'Successfully built {}'.format(image)
        logger.info(response)
        return response

    except RuntimeError as error:
        response = str(error)
        logger.error(response)
        return response

    finally:
        if os.path.exists(build_dir):
            logger.info('Removing build directory {}'.format(build_dir))
            shutil.rmtree(build_dir)


def run(conn, args):
    if len(args) < 1:
        return help()

    logger = logging.getLogger(__name__)

    image = args[0]
    logger.info('Creating container from image {}'.format(image))

    container = conn.create_container(image=image)
    if not container:
        response = 'Error creating container'
        logger.error(response)
        return response

    container_id = container.get('Id')
    short_id = container_id[0:10]

    logger.info('Starting container {}'.format(short_id))
    conn.start(container=container_id)

    logger.info('Waiting for container {} to exit'.format(short_id))
    exit_code = conn.wait(container=container_id)

    if exit_code > 0:
        response = 'Container {} returned a non-zero exit code {}'.format(short_id, exit_code)
        logger.error(response)
        return response

    response = 'Container {} exited successfully'.format(short_id)
    logger.info(response)
    return response


def docker_builder(user, action, args):
    from docker.client import Client
    from docker.utils import kwargs_from_env

    logger = logging.getLogger(__name__)
    logger.debug('ACTION {} {} {}'.format(user, action, args))

    logger.info('Connecting to Docker host')
    conn = Client(**kwargs_from_env(assert_hostname=False))

    args = args.split()

    if action == 'help':
        return help()
    elif action == 'build':
        return build_from_name(conn, args)
    elif action == 'build_url':
        return build_from_url(conn, args)
    elif action == 'run':
        return run(conn, args)


# example rich response from atlassian-jira.py
# message = {'fallback': 'jira integration', 'pretext': summary, 'color': color,
#                         'fields': [{'title': 'Issue ID', 'value': issue_url, 'short': True},
#                                    {'title': 'Assignee', 'value': assignee, 'short': True},
#                                    {'title': 'Status', 'value': status, 'short': True}, ]}

def on_push_message(msg, server):
    logger = logging.getLogger(__name__)

    pusher_name = msg.get('pusher').get('name')
    repository_url = msg.get('repository').get('git_url')

    logger.info('Received push message: {} pushed to {}'.format(pusher_name, repository_url))
    return docker_builder(pusher_name, 'build_url', repository_url)


def on_message(msg, server):
    if msg.get('pusher'):
        return on_push_message(msg, server)

    text = msg.get("text", "")
    user = msg.get("user_name", "")
    args = None

    m = re.match(r"!docker (help|build|build_url|run) ?(.*)", text)
    if not m:
        return

    action = m.group(1)
    args = m.group(2)

    return docker_builder(user, action, args)


if __name__ == '__main__':
    print "Running from cmd line"
    print docker_builder('cli', 'help', '')
    print docker_builder('cli', 'build', 'bcclaywell/docker-simple')
    #print docker_builder('cli', 'run', 'a265cc6e286a')
