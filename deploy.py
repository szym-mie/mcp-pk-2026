#!/bin/python3

import os
import sys
import time
import json
import subprocess
from collections import namedtuple
from pathlib import Path
from typing import Iterable

# use None if you want binary stdout+stderr 
ENCODING = 'utf-8'

Caller = namedtuple('Caller', 'user_id role role_arn region cluster nodegroup')
Subnet = namedtuple('Subnet', 'cidr subnet_id vpc_id zone')
Svc = namedtuple('Svc', 'hostname port')

def make_caller(role, region, cluster, nodegroup):
    return Caller(None, role, None, region, cluster, nodegroup)

# initialize color display in Windows console
if os.name == 'nt':
    os.system('color')

p = Path

# == printing, input ==
RED     = 31
GREEN   = 32
YELLOW  = 33
BLUE    = 34
MAGENTA = 35
CYAN    = 36

def cur_back(n):
    return f'\x1b[{n}D'

def sgr(code):
    return f'\x1b[{code}m'

def pr(msg, color=None):
    if color:
        msg = sgr(color) + msg + sgr(0)
    print(msg, end='', flush=True)

def ask():
    pr(sgr(CYAN) + '> ')
    res = input('')
    pr(sgr(0))
    return res

def pr_ok():
    pr('OK\n', color=GREEN)

def spin_wait(get_func, test_func, dpy_func=None, delay=10):
    time_started = time.time()
    is_time_shown = False
    dpy = None
    last_dpy_len = 0
    space = ' ' * delay
    while True:
        data = get_func()
        ok = test_func(data)
        if dpy_func:
            dpy = dpy_func(data)
            pr(f'[{dpy}] ')
            last_dpy_len = len(dpy) + 3
        if ok:
            pr('OK\n', color=GREEN)
            break
        for _ in range(delay):
            pr('|', color=MAGENTA)
            time.sleep(1)
        # redraw time elapsed counter and progress bar
        pr(cur_back(delay))
        pr(space)
        pr(cur_back(delay + last_dpy_len))
        if is_time_shown:
            pr(cur_back(7))
        is_time_shown = True
        time_elapsed = int(time.time() - time_started)
        time_unit = 's'
        if time_elapsed > 180:
            time_elapsed //= 60
            time_unit = 'm'
        pr(f'[{time_elapsed:3}{time_unit}] ')

# == deploy vals ==

class DeployVals(dict):
    def __getitem__(self, spec):
        key = None
        to_type_func = None
        if type(spec) is str:
            key = spec
        elif type(spec) is tuple:
            key, to_type_func = spec
        convert_func = to_type_func or str
        def convert(v):
            if not v:
                return None
            try:
                return convert_func(v)
            except ValueError:
                return None
        val = convert(super().get(key))
        if val:
            return val
        pr(f'Enter \'{key}\' value as {convert_func.__name__}: (bad value/missing in deploy.txt)\n')
        while not val:
            res = ask()
            val = convert(res)
            if not val:
                pr(f'Bad value for {convert_func.__name__}: {res}.\n')
        self[key] = val
        return val

def load_deploy_vals(fp):
    obj = {}
    def put(key, val):
        if key in obj:
            return False
        obj[key] = val
        return True
    for row_no, row in enumerate(fp.readlines()):
        if '#' in row:
            row = row[:row.index('#')]
        key, *tail = row.split('=')
        key = key.strip()
        if not key:
            continue
        val = None
        if len(tail) == 0:
            val = True
        elif len(tail) == 1:
            val, = tail
            val = val.strip()
        if not put(key, val):
            pr(f'Duplicate key \'{key}\' at line {row_no}: \n    {row}\n', color=MAGENTA)
            return None
    return DeployVals(obj)

# == running commands ==

def _run(args, no_json, can_fail):
    result = subprocess.run(args, encoding=ENCODING, capture_output=True)
    ret = result.returncode
    if ret > 0 and not can_fail:
        pr('!!\n', color=RED)
        cmd = ' '.join(args)
        pr(f'{cmd}\n---', color=RED)
        pr(result.stderr, color=RED)
        exit(ret)
    if no_json:
        return result.stdout
    if result.stdout:
        return json.loads(result.stdout)
    return None

def aws(*args, no_json=False, can_fail=False):
    return _run(['aws', *args, '--output=json'], no_json, can_fail)

def kube(*args, no_json=False, can_fail=False):
    return _run(['kubectl', *args, '--output=json'], no_json, can_fail)

def helm(*args, no_json=False, can_fail=False):
    extra_args = []
    if not no_json:
        extra_args.append('--output=json')
    return _run(['helm', *args, *extra_args], no_json, can_fail)

def short_struct(obj):
    obj_items_text = []
    def export_subval(subval):
        if type(subval) is bool:
            return 'true' if val else 'false'
        return str(subval)
    for key, val in obj.items():
        item_text = f'{key}='
        if type(val) is list:
            subvals_texts = [export_subval(subval) for subval in val]
            item_text += ','.join(subvals_texts)
        elif type(val) is bool:
            item_text += 'true' if val else 'false'
        else:
            item_text += str(val)
        obj_items_text.append(item_text)
    return ','.join(obj_items_text)

# == aws commands ==

def update_aws_creds(creds_rows):
    dotaws_creds_path = p.home() / '.aws' / 'credentials'
    creds_text = ''
    for row in creds_rows:
        creds_text += row + '\n'
    with open(dotaws_creds_path, 'w') as fp:
        fp.write(creds_text)

# -- getters --

def aws_list_clusters():
    return aws('eks', 'list-clusters')['clusters']

def aws_list_nodegroups(cluster):
    return aws('eks', 'list-nodegroups', '--cluster-name', cluster)['nodegroups']

def aws_get_cluster(cluster):
    cluster = aws('eks', 'describe-cluster', '--name', cluster, can_fail=True)
    if cluster:
        return cluster['cluster']
    return None

def aws_get_nodegroup(cluster, nodegroup):
    nodegroup = aws('eks', 'describe-nodegroup', '--cluster-name', cluster, '--nodegroup-name', nodegroup, can_fail=True)
    if nodegroup:
        return nodegroup['nodegroup']
    return None

def status_of(entity):
    if entity:
        return entity['status'].lower()
    return None

def aws_get_role_arn(role_name):
    role = aws('iam', 'get-role', '--role-name', role_name, can_fail=True)
    if role:
        return role['Role']['Arn']
    return None

def aws_get_all_subnets():
    subnets = aws('ec2', 'describe-subnets')['Subnets']
    return [Subnet(s['CidrBlock'], s['SubnetId'], s['VpcId'], s['AvailabilityZone']) for s in subnets]

def aws_get_cluster_subnets(cluster):
    cluster = aws('eks', 'describe-cluster', '--name', cluster, can_fail=True)
    if cluster:
        subnets_ids = cluster['cluster']['resourcesVpcConfig']['subnetIds']
        all_subnets = aws_get_all_subnets()
        def find_subnet_by_id(sub_id):
            for s in all_subnets:
                if s.subnet_id == sub_id:
                    return s
            return None
        subnets = [find_subnet_by_id(i) for i in subnets_ids]
        if all(subnets):
            return subnets
    return None

# -- creators --

def aws_create_cluster(cluster, role_arn, subnets):
    vpc_conf = {'subnetIds': [s.subnet_id for s in subnets]}
    return aws('eks', 'create-cluster', '--name', cluster, '--role-arn', role_arn, '--resources-vpc-config', short_struct(vpc_conf))

def aws_create_nodegroup(cluster, nodegroup, role_arn, subnets, nodes):
    subnets_ids = [s.subnet_id for s in subnets]
    instance, min_nodes, typ_nodes, max_nodes = nodes
    scale_conf = {'minSize': min_nodes, 'desiredSize': typ_nodes, 'maxSize': max_nodes}
    return aws('eks', 'create-nodegroup', '--cluster-name', cluster, '--nodegroup-name', nodegroup, '--node-role', role_arn, '--subnets', *subnets_ids, '--instance-types', instance, '--scaling-config', short_struct(scale_conf))

# == check caller info ==

def check_aws_user_id(caller):
    aws('configure', 'set', 'default.region', caller.region)
    caller_ident = aws('sts', 'get-caller-identity')
    user_id = caller_ident['UserId']
    return caller._replace(user_id=user_id)

def check_aws_role_arn(caller):
    role_arn = aws_get_role_arn(caller.role)
    return caller._replace(role_arn=role_arn)

def check_aws_subnets(cnt=2):
    # we have to select subnets in different availability zones
    last_zone = None
    subnets = []
    for subnet in sorted(aws_get_all_subnets(), key=lambda s: s.zone):
        if subnet.zone == last_zone:
            continue
        last_zone = subnet.zone
        subnets.append(subnet)
        if len(subnets) == cnt:
            break
    return subnets

# == kubectl ==

def update_kube_config(caller):
    aws('eks', '--region', caller.region, 'update-kubeconfig', '--name', caller.cluster, no_json=True)
    nodes_info = kube('get', 'nodes')
    return len(nodes_info['items'])

def kube_ns_arg(ns):
    if ns:
        return '-n', ns
    return ()

def is_pod_rdy(pod):
    status = pod['status']
    statuses = status.get('containerStatuses', None)
    if not statuses:
        return False
    return all([status['ready'] for status in statuses])

def kube_get_pods(ns=None):
    res = kube(*kube_ns_arg(ns), 'get', 'pods')
    if res:
        return res['items']
    return []

def kube_new_ns(ns):
    return kube('create', 'ns', ns, no_json=True, can_fail=True)

def kube_apply(url, ns=None):
    return kube(*kube_ns_arg(ns), 'apply', '-f', url)

def kube_patch(what, name, how, patch, ns=None):
    return kube(*kube_ns_arg(ns), 'patch', what, name, '--type', how, '-p', json.dumps(patch))

def kube_list_svc(ns=None):
    res = kube(*kube_ns_arg(ns), 'get', 'svc', can_fail=True)
    svcs = {}
    if res:
        for svc in res['items']:
            name = svc['metadata']['name']
            ports = svc['spec']['ports']
            lb = svc['status']['loadBalancer']
            hostname = None
            port = None
            ingress = lb.get('ingress')
            if ingress:
                hostname = ingress[0]['hostname']
            for port_info in ports:
                if port_info['name'] == 'http':
                    port = port_info['port']
                    break
            if hostname and port:
                svcs[name] = Svc(hostname, port)
    return svcs

# == helm ==

def helm_repo_add(repo, url):
    return helm('repo', 'add', repo, url, no_json=True)

def helm_repo_update():
    return helm('repo', 'update', no_json=True)

def helm_install(name, repo, chart, opts, ns=None):
    opts_args = []
    for key, val in opts.items():
        opts_args += ['--set', f'{key}={val}']
    return helm(*kube_ns_arg(ns), 'install', name, f'{repo}/{chart}', *opts_args, no_json=True)

def helm_is_rdy(name, ns=None):
    items = helm(*kube_ns_arg(ns), 'list')
    for item in items:
        if item['name'] == name:
            return item['status'] == 'deployed'
    return None

# == various utils ==

def pods_rdy_cnt(pods):
    all_pods_cnt = len(pods)
    rdy_pods_cnt = sum([is_pod_rdy(pod) for pod in pods])
    return rdy_pods_cnt, all_pods_cnt

def are_pods_rdy(pods):
    rdy_cnt, all_cnt = pods_rdy_cnt(pods)
    return rdy_cnt == all_cnt

def pr_pods_rdy_cnt(pods):
    rdy_cnt, all_cnt = pods_rdy_cnt(pods)
    all_cnt = str(all_cnt)
    digits = len(all_cnt)
    rdy_cnt = str(rdy_cnt).rjust(digits, ' ')
    return f'{rdy_cnt}/{all_cnt}'

def ns_pods_rdy(ns):
    return lambda: kube_get_pods(ns), are_pods_rdy, pr_pods_rdy_cnt 

# == main ==

def parse_args(args):
    fast, use_lb, dvf = False, False, p('deploy.txt')
    state = 'reset'
    for arg in args:
        if state == 'reset':
            if arg == '-fast':
                fast = True
            if arg == '-lb':
                use_lb = True
            if arg == '-file':
                state = 'dvf'
            if arg == '-h':
                pr('deploy.py ARGS...\n')
                pr('Arguments:\n')
                pr('-fast\n')
                pr('  Postpones the readiness checks until all configs\n')
                pr('  are applied, somewhat reducing the waiting times\n')
                pr('  during the setup.\n')
                pr('-lb\n')
                pr('  Patch services so they use load balancers, alleviating\n')
                pr('  the need fot port-forwarding.\n')
                pr('-file FILE\n')
                pr('  Load deploy vals (basically the deployment config)\n')
                pr('  from the FILE, instead of deploy.txt in the current\n')
                pr('  directory.\n')
                pr('-h\n')
                pr('  Display this help message.\n')
                exit(0)
        elif state == 'dvf':
            dvf = p(arg)
            state = 'reset'
    if state != 'reset':
        pr(f'Incomplete list of args - was expecting {state}. Abort.\n', color=RED)
        exit(1)
    return fast, use_lb, dvf

def main():
    _, *args = sys.argv
    fast, use_lb, dvf = parse_args(args)

    if fast:
        pr('Using fast checking.\n')
    if use_lb:
        pr('Using LoadBalancers.\n')
    pr(f'Using {dvf} for deploy vals.\n')
    dv = DeployVals({})
    with open(dvf) as fp:
        dv = load_deploy_vals(fp)
    if not dv:
        pr('Cannot load deploy vals. Abort.\n', color=RED)
        exit(2)

    caller = make_caller(dv['role'], dv['region'], dv['cluster_name'], dv['nodegroup_name'])
    
    # aws credentials input
    pr('Copy and paste your AWS CLI credentials below.\n')
    pr('After pasting, to save the credentials, hit Enter again.\n', color=CYAN)
    # pr('To reenter the credentials, type \'!\' and hit Enter.\n', color=CYAN)
    pr('If you don\'t need to update the credentials, leave the field empty\n')
    pr('and press Enter. This will skip this step entirely.\n')
    creds_rows = []
    while len(creds_rows) < 1 or creds_rows[-1]:
        creds_rows.append(ask())
    if len(creds_rows) > 1:
        update_aws_creds(creds_rows)
    else:
        pr('Skipping credentials.\n', color=CYAN)
    
    # complete caller info
    pr('Obtaining user ID... ')
    caller = check_aws_user_id(caller)
    pr_ok()
    pr('Obtaining role ARN... ')
    caller = check_aws_role_arn(caller)
    pr_ok()

    pr('Caller info:\n')
    pr(f'User ID     {caller.user_id}\n')
    pr(f'Role        {caller.role}\n')
    pr(f'Role ARN    {caller.role_arn}\n')
    pr(f'Region      {caller.region}\n')
    pr(f'Cluster     {caller.cluster}\n')
    pr(f'Node group  {caller.nodegroup}\n')
    pr('---\n')

    def get_cluster_status():
        return status_of(aws_get_cluster(caller.cluster))

    def get_nodegroup_status():
        return status_of(aws_get_nodegroup(caller.cluster, caller.nodegroup))

    def pr_stat(stat):
        if not stat:
            pr('notexist', color=RED)
        elif stat != 'active':
            pr(stat, color=CYAN)
        else:
            pr(stat, color=GREEN)
    
    # check if cluster is already up or gone
    pr('Checking machines... ')
    cl_stat = get_cluster_status()
    ng_stat = get_nodegroup_status()
    pr('Cluster: ')
    pr_stat(cl_stat)
    pr('  Node group: ')
    pr_stat(ng_stat)
    pr('\n')
    if cl_stat and cl_stat != 'active':
        pr('Waiting... ')
        spin_wait(get_cluster_status, lambda s: not s or s == 'active')
        cl_stat = get_cluster_status()
    
    # prepare eks
    if not cl_stat:
        subnet_cnt = dv['subnets', int]
        pr('Obtaining subnets... ')
        subnets = check_aws_subnets(subnet_cnt)
        pr_ok()
        pr('Subnets:\n')
        for cidr, _, _, zone in subnets:
            pr(f': {cidr} ({zone})\n')
        pr('---\n')

        pr(f'Creating cluster... ')
        aws_create_cluster(caller.cluster, caller.role_arn, subnets)
        pr_ok()
        pr('Waiting for the cluster to become ready... ')
        spin_wait(get_cluster_status, lambda s: s == 'active')
    
    if ng_stat and ng_stat != 'active':
        pr('Waiting... ')
        spin_wait(get_nodegroup_status, lambda s: not s or s == 'active')
        ng_stat = get_nodegroup_status()

    if not ng_stat:
        subnets = aws_get_cluster_subnets(caller.cluster)
        pr(f'Creating nodegroup... ')
        min_nodes = dv['min_nodes', int]
        typ_nodes = dv['typ_nodes', int]
        max_nodes = dv['max_nodes', int]
        nodes = dv['machine_type'], min_nodes, typ_nodes, max_nodes
        aws_create_nodegroup(caller.cluster, caller.nodegroup, caller.role_arn, subnets, nodes)
        pr_ok()

    # update kubectl config
    pr('Updating kubectl config... ')
    nodes_cnt = update_kube_config(caller)
    pr_ok()
    pr(f'Kube: {nodes_cnt} nodes up.\n')
    pr('Waiting for system pods to initialize... ')
    spin_wait(*ns_pods_rdy('kube-system'))

    # creating namespaces
    APP_NS = dv['app_ns']
    LLM_NS = dv['llm_ns']
    MON_NS = dv['mon_ns']
    NSS = APP_NS, LLM_NS, MON_NS
    pr('Creating namespaces... ')
    for ns in NSS:
        kube_new_ns(APP_NS)
        pr(f'{ns} ', color=GREEN)
    pr('\n---\n')

    # deploy microservices app
    pr('Applying microservices-demo... ')
    kube_apply(dv['app_url'], ns=APP_NS)
    pr_ok()
    if not fast:
        pr('Awaiting app readiness... ')
        spin_wait(*ns_pods_rdy(APP_NS))
    pr('---\n')

    # deploy ollama+openwebui
    pr('Applying ollama... ')
    kube_apply(dv['ollama_url'], ns=LLM_NS)
    pr_ok()
    pr('Applying open-webui... ')
    kube_apply(dv['webui_url'], ns=LLM_NS)
    pr_ok()
    if use_lb:
        pr('Patching in LoadBalancers... ')
        lb_patch = dv['lb_patch', json.loads]
        kube_patch('svc', 'open-webui', 'merge', lb_patch, ns=LLM_NS)
        kube_patch('svc', 'ollama', 'merge', lb_patch, ns=LLM_NS)
        pr_ok()
    if not fast:
        pr('Awaiting agent readiness... ')
        spin_wait(*ns_pods_rdy(LLM_NS))
    pr('---\n')

    # deploy prometheus+grafana
    HELM_MON_NAME = dv['helm_mon_name']
    HELM_MON_REPO = dv['helm_mon_repo']
    if helm_is_rdy(HELM_MON_NAME, ns=MON_NS) is None:
        pr('Adding prometheus-community repo... ')
        helm_repo_add(HELM_MON_REPO, dv['promgraf_url'])
        pr_ok() 
        pr('Updating helm repos... ')
        helm_repo_update()
        pr_ok() 
        pr('Installing kube-prometheus-stack... ')
        opts = dv['helm_mon_opts', json.loads]
        helm_install(HELM_MON_NAME, HELM_MON_REPO, dv['helm_mon_chart'], opts, ns=MON_NS)
        pr_ok()
    if not fast:
        pr('Awaiting monitoring readiness... ')
        spin_wait(*ns_pods_rdy(MON_NS))
    pr('---\n')
    
    if fast:
        pr('Awaiting app readiness... ')
        spin_wait(*ns_pods_rdy(APP_NS))
        pr('Awaiting agent readiness... ')
        spin_wait(*ns_pods_rdy(LLM_NS))
        pr('Awaiting monitoring readiness... ')
        spin_wait(*ns_pods_rdy(MON_NS))
        pr('---\n')

    # list services
    pr('Listing services... ')
    svcss = []
    for ns in NSS:
        svcss.append(kube_list_svc(ns=ns))
        pr(f'{ns} ')
    pr('\n')
    pr('Services:\n')
    for svcs in svcss: 
        for name, svc in svcs.items():
            pr(f'- {name}\n')
            pr(f'  {svc.hostname}:{svc.port}\n', color=CYAN)
    pr('---\n')
    if not use_lb:
        pr('LoadBalancers were not patched in.\n', color=YELLOW)

    pr('WIP')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pr('Exit.\n', color=MAGENTA)
    exit(0)

