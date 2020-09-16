#!/usr/bin/env python3

# Copyright 2015-present MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Requires mongo-orchestration running on port 8889.
#
# Usage:
# python launch.py <single|repl|shard> <auth> <ssl>
#
# Examples (standalone node):
# python ~/launch.py single
# python ~/launch.py single auth
# python ~/launch.py single auth ssl
#
# Sharded clusters:
# python ~/launch.py shard
# python ~/launch.py shard auth
# python ~/launch.py shard auth ssl
#
# Replica sets:
# python ~/launch.py repl
# python ~/launch.py repl single
# python ~/launch.py repl single auth

import atexit
import copy
import itertools
import time
import os
import sys

try:
    import pymongo
except ImportError:
    raise ImportError('pymongo is not installed, install it with:\n'
                      '%s -m pip install pymongo' % (sys.executable,))

try:
    import requests
except ImportError:
    raise ImportError('requests is not installed, install it with:\n'
                      '%s -m pip install requests' % (sys.executable,))


if sys.version_info[0] == 3:
    raw_input = input
    unicode = str

# Configurable hosts and ports used in the tests
db_user = unicode(os.environ.get("DB_USER", ""))
db_password = unicode(os.environ.get("DB_PASSWORD", ""))


_mo_address = os.environ.get("MO_ADDRESS", "localhost:8889")
_mongo_start_port = int(os.environ.get("MONGO_PORT", 27017))
_free_port = itertools.count(_mongo_start_port)

DEFAULT_OPTIONS = {
    'logappend': True,
    'ipv6': True,
    'bind_ip': '127.0.0.1,::1',
    # 'storageEngine': 'mmapv1',
    # 'networkMessageCompressors': 'disabled',
    # 'vvvvv': '',
    'setParameter': {
        'enableTestCommands': 1,
        # 'ocspEnabled': 'true',
        # 'failpoint.disableStapling': '{"mode":"alwaysOn"}',
    },  # 'logicalSessionRefreshMillis': 1000000},
}


_post_request_template = {}
if db_user and db_password:
    _post_request_template = {'login': db_user, 'password': db_password}


def _mo_url(resource, *args):
    return 'http://' + '/'.join([_mo_address, resource] + list(args))


def shutdown_all():
    clusters = requests.get(_mo_url('sharded_clusters')).json()
    repl_sets = requests.get(_mo_url('replica_sets')).json()
    servers = requests.get(_mo_url('servers')).json()
    for cluster in clusters['sharded_clusters']:
        requests.delete(_mo_url('sharded_clusters', cluster['id']))
    for rs in repl_sets['replica_sets']:
        requests.delete(_mo_url('relica_sets', rs['id']))
    for server in servers['servers']:
        requests.delete(_mo_url('servers', server['id']))


class MCTestObject(object):

    def proc_params(self):
        params = copy.deepcopy(DEFAULT_OPTIONS)
        params.update(self._proc_params)
        params["port"] = next(_free_port)
        return params

    def get_config(self):
        raise NotImplementedError

    def _make_post_request(self):
        config = _post_request_template.copy()
        config.update(self.get_config())
        import pprint
        pprint.pprint(config)
        ret = requests.post(
            _mo_url(self._resource), timeout=None, json=config)#.json()

        if not ret.ok:
            raise RuntimeError(
                "Error sending POST to cluster: %s" % (ret.text,))

        ret = ret.json()
        if type(ret) == list:  # Will return a list if an error occurred.
            raise RuntimeError("Error sending POST to cluster: %s" % (ret,))
        pprint.pprint(ret)
        return ret

    def _make_get_request(self):
        ret = requests.get(_mo_url(self._resource, self.id), timeout=None)

        if not ret.ok:
            raise RuntimeError(
                "Error sending GET to cluster: %s" % (ret.text,))

        ret = ret.json()
        if type(ret) == list:  # Will return a list if an error occurred.
            raise RuntimeError("Error sending GET to cluster: %s" % (ret,))
        return ret

    def client(self, **kwargs):
        client = pymongo.MongoClient(self.uri, **kwargs)
        if db_user:
            client.admin.authenticate(db_user, db_password)
        return client

    def stop(self):
        requests.delete(_mo_url(self._resource, self.id))


class Server(MCTestObject):

    _resource = 'servers'

    def __init__(self, id=None, uri=None, **kwargs):
        self.id = id
        self.uri = uri
        self._proc_params = kwargs

    def get_config(self):
        return {
            'name': 'mongod',
            'procParams': self.proc_params()}

    def start(self):
        if self.id is None:
            response = self._make_post_request()
            self.id = response['id']
            self.uri = response.get('mongodb_auth_uri',
                                    response['mongodb_uri'])
        else:
            requests.post(
                _mo_url('servers', self.id), timeout=None,
                json={'action': 'start'}
            )
        return self

    def stop(self, destroy=True):
        if destroy:
            super(Server, self).stop()
        else:
            requests.post(_mo_url('servers', self.id), timeout=None,
                          json={'action': 'stop'})

    def _init_from_response(self, response):
        self.id = response['id']
        self.uri = response.get('mongodb_auth_uri', response['mongodb_uri'])
        return self


class ReplicaSet(MCTestObject):

    _resource = 'replica_sets'

    def __init__(self, id=None, uri=None, primary=None, secondary=None,
                 single=False, **kwargs):
        self.single = single
        self.id = id
        self.uri = uri
        self.primary = primary
        self.secondary = secondary
        self._proc_params = kwargs
        self.members = []

    def proc_params(self):
        params = super(ReplicaSet, self).proc_params()
        # params.setdefault('setParameter', {}).setdefault('transactionLifetimeLimitSeconds', 3)
        # params.setdefault('setParameter', {}).setdefault('periodicNoopIntervalSecs', 1)
        # params.setdefault('setParameter', {})['failpoint'] = '{"disableStapling":{"mode":"alwaysOn"}}'
        return params

    def get_config(self):
        members = [{'procParams': self.proc_params()}]
        if not self.single:
            members.extend([
                {'procParams': self.proc_params()},
                {'rsParams': {'arbiterOnly': False},
                 'procParams': self.proc_params()}
            ])
        return {'members': members}

    def _init_from_response(self, response):
        self.id = response['id']
        self.uri = response.get('mongodb_auth_uri', response['mongodb_uri'])
        for member in response['members']:
            m = Server(member['server_id'], member['host'])
            self.members.append(m)
            if member['state'] == 1:
                self.primary = m
            elif member['state'] == 2:
                self.secondary = m
        return self

    def start(self):
        # We never need to restart a replica set, only start new ones.
        return self._init_from_response(self._make_post_request())

    def restart_primary(self):
        self.primary.stop(destroy=False)
        time.sleep(5)
        self.primary.start()
        time.sleep(1)
        self._init_from_response(self._make_get_request())
        print('New primary: %s' % self.primary.uri)


class ReplicaSet2(ReplicaSet):

    def get_config(self):
        return {
            'members': [
                {'procParams': self.proc_params()},
                {'procParams': self.proc_params()},
            ]
        }


class ReplicaSetSingle(ReplicaSet):

    def get_config(self):
        return {
            'members': [
                {'procParams': self.proc_params()}
            ]
        }


class ShardedCluster(MCTestObject):

    _resource = 'sharded_clusters'
    _shard_type = ReplicaSet

    def __init__(self, **kwargs):
        self.id = None
        self.uri = None
        self.shards = []
        self._proc_params = kwargs

    def get_config(self):
        return {
            # 'configsvrs': [{'members': [DEFAULT_OPTIONS.copy()]}],
            'routers': [self.proc_params(), self.proc_params()],
            'shards': [
                {'id': 'demo-set-0', 'shardParams':
                    self._shard_type().get_config()},
                # {'id': 'demo-set-1', 'shardParams':
                #     self._shard_type().get_config()}
            ]
        }

    def start(self):
        # We never need to restart a sharded cluster, only start new ones.
        response = self._make_post_request()
        for shard in response['shards']:
            shard_resp = requests.get(_mo_url(self._shard_type()._resource, shard['_id']))
            if not shard_resp.ok:
                raise RuntimeError(
                    "Error getting shard info: %s" % (shard_resp.text,))

            shard_json = shard_resp.json()
            self.shards.append(self._shard_type()._init_from_response(shard_json))
        self.id = response['id']
        self.uri = response.get('mongodb_auth_uri', response['mongodb_uri'])
        return self


class ShardedCluster4(ShardedCluster):
    _shard_type = ReplicaSet2
    def get_config(self):
        return {
            'routers': [self.proc_params(), self.proc_params()],
            'shards': [
                {'id': 'demo-set-0', 'shardParams':
                    self._shard_type().get_config()},
                {'id': 'demo-set-1', 'shardParams':
                    self._shard_type().get_config()},
                {'id': 'demo-set-3', 'shardParams':
                    self._shard_type().get_config()},
                {'id': 'demo-set-4', 'shardParams':
                    self._shard_type().get_config()},
            ]
        }


class ShardedClusterSingle(ShardedCluster):
    _shard_type = ReplicaSetSingle


def argv_has(string):
    return any(string in arg for arg in sys.argv[1:])


CERTS = '/Users/shane/git/mongo-python-driver/test/certificates/'
# CERTS = 'C:\\cygwin\\home\\Administrator\\mongo-python-driver\\test\\certificates\\'

if __name__ == '__main__':
    for arg in sys.argv[1:]:
        try:
            port = int(arg)
            _free_port = itertools.count(port)
        except:
            pass
    for version in ['2.6.12', '3.0.12', '3.2.10', '3.4.0-rc0']:
        if argv_has(version):
            _post_request_template['version'] = version
            break

    if argv_has('ssl') or argv_has('tls'):
        _post_request_template['sslParams'] = {
            "sslOnNormalPorts": True,
            "sslPEMKeyFile": CERTS + "server.pem",
            "sslCAFile": CERTS + "ca.pem",
            "sslWeakCertificateValidation": True
        }
    if argv_has('auth'):
        _post_request_template['login'] = db_user or 'user'
        _post_request_template['password'] = db_password or 'password'
        _post_request_template['auth_key'] = 'secret'

    single = argv_has('single') or argv_has('standalone') or argv_has('mongod')
    if argv_has('repl'):
        # DEFAULT_OPTIONS['enableMajorityReadConcern'] = ''
        cluster = ReplicaSet(single=single)
    elif argv_has('shard4'):
        cluster = ShardedCluster4()
    elif argv_has('shard3'):
        cluster = ShardedCluster()
    elif argv_has('shard') or argv_has('mongos'):
        cluster = ShardedClusterSingle()
    elif single:
        cluster = Server()
    else:
        exit('Usage: %s [single|replica|shard] [ssl] [auth]' % (__file__,))

    atexit.register(shutdown_all)
    cluster.start()
    while True:
        data = raw_input('Type "q" to quit, "r" to shutdown and restart the primary": ')
        if data == 'q':
            break
        if data == 'r':
            cluster.restart_primary()
    cluster.stop()
