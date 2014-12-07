"""usage: !docker <command> [<args>]

Available commands:
   build <repository url>          checkout and build an image from a git repository
   help                            show this help
   run <image id>                  create and run a container
"""

# structure borrowed from atlassian-jira.py

import re
import envoy
import tempfile

def help():
    return __doc__

def build(conn, args):
    # regex from Kel Solaar, http://stackoverflow.com/a/22312124
    m = re.match('(((git|ssh|git\+ssh|http(s)?)|(git@[\w\.]+))(:(//)?)([\w\.@\:/\-~]+)(\.git)(/)?)', args)
    if not m:
        return help()

    repository_url = m.group(1)
    build_dir = tempfile.mkdtemp()

    print 'cloning {}'.format(repository_url)
    r = envoy.run('git clone {} {}'.format(repository_url, build_dir))
    if r.status_code > 0:
        response = 'clone error {}: {}\n{}'.format(r.status_code, r.std_out, r.std_err)
        return response

    print 'building image'
    build_output = [line for line in conn.build(path=build_dir, rm=True)]

    # check the last line of the build output for success
    m = re.search('Successfully built ([0-9a-f]+)', build_output[-1])
    if not m:
        response = 'build error: {}'.format(build_output[-1])
        return response
    image = m.group(1)

    response = 'successfully built {}'.format(image)
    return response

def run(conn, args):
    m = re.match('([0-9a-f]+)', args)
    if not m:
        return help()

    image = m.group(1)

    print 'creating container'
    container = conn.create_container(image=image)
    if not container:
        response = 'error creating container'
        return response

    print 'starting container'
    response = conn.start(container=container.get('Id'))

    print 'waiting for container to exit'
    exit_code = conn.wait(container=container.get('Id'))

    if exit_code > 0:
        response = 'container process returned non-zero exit code {}'.format(exit_code)
        return response

    response = 'success'
    return response


def docker_builder(user, action, args):
    from docker import Client
    conn = Client()

    if action == 'help':
        return help()
    elif action == 'build':
        return build(conn, args)
    elif action == 'run':
        return run(conn, args)


# example rich response from atlassian-jira.py
# message = {'fallback': 'jira integration', 'pretext': summary, 'color': color,
#                         'fields': [{'title': 'Issue ID', 'value': issue_url, 'short': True},
#                                    {'title': 'Assignee', 'value': assignee, 'short': True},
#                                    {'title': 'Status', 'value': status, 'short': True}, ]}

def on_message(msg, server):
    text = msg.get("text", "")
    user = msg.get("user_name", "")
    args = None
    m = re.match(r"!docker (help|build|run) ?(.*)", text)
    if not m:
        return

    action = m.group(1)
    args = m.group(2)
    return docker_builder(user, action, args)


if __name__ == '__main__':
    print "Running from cmd line"
    print docker_builder('cli', 'help', '')
    print docker_builder('cli', 'build', 'git+ssh://github.com/bcclaywell/pplacer-builder.git')
    print docker_builder('cli', 'run', 'fa20e0548551')

