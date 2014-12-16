"""usage: !docker <command> [<args>]

Available commands:
   help                            show this help
   build <github repository>       build an image from a github repository
   run <image id>                  create and run a container
"""

import envoy
import json
import logging
import os
import re
import requests
import shutil
import tempfile
import threading
from config import config
from Queue import Queue


class DockerBuilder(threading.Thread):
    queue = Queue()

    def __init__(self):
        super(DockerBuilder, self).__init__()
        self.daemon = True
        self.cancelled = False

        logging.basicConfig(level=logging.DEBUG)
        self.logger = logging.getLogger(__name__)

        self.queue = DockerBuilder.queue
        self.build_dirs = {}

        from docker.client import Client
        from docker.utils import kwargs_from_env

        self.logger.info('Connecting to Docker host')
        self.client = Client(**kwargs_from_env(assert_hostname=False))

        import atexit
        atexit.register(self.cleanup_build_dirs)


    def run(self):
        self.logger.info('Thread started')

        while not self.cancelled:
            self.logger.info('Waiting for task')

            # block until a task is available
            task = self.queue.get(True)

            self.logger.info('Processing task')

            user = task['user']
            action = task['action']
            args = task['args']

            response = None

            if action == 'build':
                response = self.build_image_github(args)
            elif action == 'run':
                response = self.run_image(args)
            else:
                response = 'Unknown action {}'.format(action)

            try:
                send_msg(response)
            except Exception as error:
                self.logger.error('An unhandled exception occurred: ' + str(error))

            self.queue.task_done()


    def cancel(self):
        self.cancelled = True


    def prepare_build_dir(self, repository_url):
        build_dir = None

        if repository_url in self.build_dirs:
            build_dir = self.build_dirs.get(repository_url)
            self.logger.info('Pulling from {} into {}'.format(repository_url, build_dir))
            update_repository(build_dir)
        else:
            build_dir = tempfile.mkdtemp(dir=config.get('builder_dir', None))
            self.logger.info('Cloning {} to {}'.format(repository_url, build_dir))
            clone_repository(repository_url, build_dir)
            self.build_dirs[repository_url] = build_dir

        return build_dir


    def cleanup_build_dirs(self):
        for build_dir in self.build_dirs.values():
            if os.path.exists(build_dir):
                self.logger.info('Removing build directory {}'.format(build_dir))
                shutil.rmtree(build_dir)


    def build_image_github(self, args):
        if len(args) < 1:
            return usage()

        repository = args[0]
        repository_url = 'git+ssh://github.com/{}.git'.format(repository)

        response = self.build_image_url([repository_url])
        response = '[{}] {}'.format(repository, response)
        return response


    def build_image_url(self, args):
        if len(args) < 1:
            return usage()

        # regex originally from Kel Solaar, http://stackoverflow.com/a/22312124
        m = re.match('((git(\+ssh)?|ssh|http(s)?)|(git@[\w\.]+))(:(//)?)([\w\.@\:/\-~]+)(\.git)(/)?', args[0])
        if not m:
            return usage()

        repository_url = m.group(0)
        response = None

        try:
            build_dir = self.prepare_build_dir(repository_url)

            self.logger.info('Building image')
            build_output = [line for line in self.client.build(path=build_dir, rm=True)]

            # envoy-based runner in case of trouble with docker-py
            #r = envoy.run('docker build {}'.format(build_dir))
            #build_output = [line for line in r.std_out.split('\n') if line]

            # check the build output for success
            m = re.search('Successfully built ([0-9a-f]+)', build_output[-1])
            if not m:
                raise RuntimeError('Error building image: {}'.format(build_output[-1]))

            image = m.group(1)
            response = 'Successfully built {}'.format(image)
            self.logger.info(response)
            return response

        except RuntimeError as error:
            response = str(error)
            self.logger.error(response)
            return response


    def run_image(self, args):
        if len(args) < 1:
            return usage()

        image = args[0]
        self.logger.info('Creating container from image {}'.format(image))

        container = self.client.create_container(image=image)
        if not container:
            response = 'Error creating container'
            self.logger.error(response)
            return response

        container_id = container.get('Id')
        short_id = container_id[0:10]

        self.logger.info('Starting container {}'.format(short_id))
        self.client.start(container=container_id)

        self.logger.info('Waiting for container {} to exit'.format(short_id))
        exit_code = self.client.wait(container=container_id)

        if exit_code > 0:
            response = 'Container {} returned a non-zero exit code {}'.format(short_id, exit_code)
            self.logger.error(response)
            return response

        response = 'Container {} exited successfully'.format(short_id)
        self.logger.info(response)
        return response


#####


def usage():
    return __doc__


def clone_repository(repository_url, build_dir):
    r = envoy.run('git clone {} {}'.format(repository_url, build_dir))
    if r.status_code is not 0:
        raise RuntimeError('git returned a non-zero status code ({}):\n{}\n{}'
                           .format(r.status_code, r.std_out, r.std_err))


def update_repository(build_dir):
    r = envoy.run('cd {} && git pull origin master'.format(build_dir))
    if r.status_code is not 0:
        raise RuntimeError('git returned a non-zero status code ({}):\n{}\n{}'
                           .format(r.status_code, r.std_out, r.std_err))


# example rich response from atlassian-jira.py
# message = {'fallback': 'jira integration', 'pretext': summary, 'color': color,
#                         'fields': [{'title': 'Issue ID', 'value': issue_url, 'short': True},
#                                    {'title': 'Assignee', 'value': assignee, 'short': True},
#                                    {'title': 'Status', 'value': status, 'short': True}, ]}

def send_msg(msg, channel=None):
    if channel is None:
        channel = config.get('builder_channel')

    username = config.get('username')
    icon_url = config.get('icon_url')
    webhook_url = config.get('webhook_url')

    payload = { 'channel': channel,
                'username': username,
                'icon_url': icon_url,
                'text': msg }

    r = requests.post(webhook_url, data=json.dumps(payload), timeout=5)

#####

worker = DockerBuilder()

#####

def on_message(msg, server):
    if not worker.is_alive():
        worker.start()

    logger = logging.getLogger(__name__)
    logger.debug('on_message: {}'.format(msg))

    task = None

    if msg.get('repository') and msg.get('pusher'):
        # push notification
        user = msg.get('pusher').get('name')
        repository_name = msg.get('repository').get('full_name')

        task = { 'user': user,
                 'action': 'build',
                 'args': [repository_name] }
    else:
        # channel message
        text = msg.get("text", "")
        user = msg.get("user_name", "")
        args = None

        m = re.match(r"!docker (help|build|run) ?(.*)", text)
        if not m:
            return

        action = m.group(1)
        args = m.group(2).split()
        task = { 'user': user,
                 'action': action,
                 'args': args }

    if not task:
        return

    if task.get('action') == 'help':
        return usage()
    else:
        DockerBuilder.queue.put(task)
        logger.info('Queuing task')


if __name__ == '__main__':
    print "Running from cmd line"

    #worker = DockerBuilder()
    #worker.start()

    push_msg = { 'pusher': { 'name': 'bcclaywell' },
                       'repository': { 'full_name': 'bcclaywell/docker-simple' } }

    command_msg = { 'user_name': 'bcclaywell',
                       'text': '!docker build bcclaywell/docker-simple' }

    print on_message(push_msg, None)
    print on_message(command_msg, None)

    # wait for tasks to finish
    DockerBuilder.queue.join()
