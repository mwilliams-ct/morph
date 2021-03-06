# distbuild/worker_build_scheduler.py -- schedule worker-builds on workers
#
# Copyright (C) 2012, 2014  Codethink Limited
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA..


import collections
import httplib
import logging
import socket
import urllib
import urlparse

import distbuild


class WorkerBuildRequest(object):

    def __init__(self, artifact, initiator_id):
        self.artifact = artifact
        self.initiator_id = initiator_id

class WorkerCancelPending(object):
    
    def __init__(self, initiator_id):
        self.initiator_id = initiator_id    

class WorkerBuildStepStarted(object):

    def __init__(self, initiators, cache_key, worker_name):
        self.initiators = initiators
        self.artifact_cache_key = cache_key
        self.worker_name = worker_name

class WorkerBuildStepAlreadyStarted(object):

    def __init__(self, initiator_id, cache_key, worker_name):
        self.initiator_id = initiator_id
        self.artifact_cache_key = cache_key
        self.worker_name = worker_name

class WorkerBuildWaiting(object):

    def __init__(self, initiator_id, cache_key):
        self.initiator_id = initiator_id
        self.artifact_cache_key = cache_key

class WorkerBuildOutput(object):

    def __init__(self, msg, cache_key):
        self.msg = msg
        self.artifact_cache_key = cache_key

class WorkerBuildCaching(object):

    def __init__(self, initiators, cache_key):
        self.initiators = initiators
        self.artifact_cache_key = cache_key

class WorkerBuildFinished(object):

    def __init__(self, msg, cache_key):
        self.msg = msg
        self.artifact_cache_key = cache_key
        
class WorkerBuildFailed(object):

    def __init__(self, msg, cache_key):
        self.msg = msg
        self.artifact_cache_key = cache_key


class _NeedJob(object):

    def __init__(self, who):
        self.who = who
        

class _HaveAJob(object):

    def __init__(self, job):
        self.job = job

class Job(object):

    def __init__(self, job_id, artifact, initiator_id):
        self.id = job_id
        self.artifact = artifact
        self.initiators = [initiator_id]
        self.who = None  # we don't know who's going to do this yet
        self.running = False
        self.failed = False


class Jobs(object):

    def __init__(self, idgen):
        self._idgen = idgen
        self._jobs = {}

    def get(self, artifact_basename):
        return (self._jobs[artifact_basename]
            if artifact_basename in self._jobs else None)

    def create(self, artifact, initiator_id):
        job = Job(self._idgen.next(), artifact, initiator_id)
        self._jobs[job.artifact.basename()] = job
        return job

    def remove(self, job):
        if job.artifact.basename() in self._jobs:
            del self._jobs[job.artifact.basename()]
        else:
            logging.warning("Tried to remove a job that doesn't exist "
                            "(%s)", job.artifact.basename())

    def get_jobs(self):
        return self._jobs

    def remove_jobs(self, jobs):
        for job in jobs:
            self.remove(job)

    def exists(self, artifact_basename):
        return artifact_basename in self._jobs

    def get_next_job(self):
        # for now just return the first thing we find that's not being built
        waiting = [job for (_, job) in
            self._jobs.iteritems() if job.who == None]

        return waiting.pop() if len(waiting) > 0 else None

    def __repr__(self):
        return str([job.artifact.basename()
            for (_, job) in self._jobs.iteritems()])


class _BuildFinished(object):

    pass


class _BuildFailed(object):

    pass


class _BuildCancelled(object):

    pass

        
class _Cached(object):

    pass


class _JobStarted(object):

    def __init__(self, job):
        self.job = job


class _JobFinished(object):

    def __init__(self, job):
        self.job = job


class _JobFailed(object):

    def __init__(self, job):
        self.job = job
    
class WorkerBuildQueuer(distbuild.StateMachine):

    '''Maintain queue of outstanding worker-build requests.
    
    This state machine captures WorkerBuildRequest events, and puts them
    into a queue. It also catches _NeedJob events, from a
    WorkerConnection, and responds to them with _HaveAJob events,
    when it has an outstanding request.
    
    '''
    
    def __init__(self):
        distbuild.StateMachine.__init__(self, 'idle')

    def setup(self):
        distbuild.crash_point()

        logging.debug('WBQ: Setting up %s' % self)
        self._available_workers = []
        self._jobs = Jobs(
            distbuild.IdentifierGenerator('WorkerBuildQueuerJob'))
        
        spec = [
            # state, source, event_class, new_state, callback
            ('idle', WorkerBuildQueuer, WorkerBuildRequest, 'idle',
                self._handle_request),
            ('idle', WorkerBuildQueuer, WorkerCancelPending, 'idle',
                self._handle_cancel),

            ('idle', WorkerConnection, _NeedJob, 'idle', self._handle_worker),
            ('idle', WorkerConnection, _JobStarted, 'idle',
                self._set_job_started),
            ('idle', WorkerConnection, _JobFinished, 'idle',
                self._set_job_finished),
            ('idle', WorkerConnection, _JobFailed, 'idle',
                self._set_job_failed)
        ]
        self.add_transitions(spec)

    def _set_job_started(self, event_source, event):
        logging.debug('Setting job state for job %s with id %s: '
                      'Job is running',
                      event.job.artifact.basename(), event.job.id)

        event.job.running = True

    def _set_job_finished(self, event_source, event):
        logging.debug('Setting job state for job %s with id %s: '
                      'Job is NOT running',
                      event.job.artifact.basename(), event.job.id)

        event.job.running = False

    def _set_job_failed(self, event_source, event):
        logging.debug('Job %s with id %s failed',
                      event.job.artifact.basename(), event.job.id)
        event.job.failed = True

    def _handle_request(self, event_source, event):
        distbuild.crash_point()

        logging.debug('Handling build request for %s' % event.initiator_id)
        logging.debug('Current jobs: %s' % self._jobs)
        logging.debug('Workers available: %d' % len(self._available_workers))

        # Have we already made a job for this thing?
        # If so, add our initiator id to the existing job
        # If not, create a job

        if self._jobs.exists(event.artifact.basename()):
            job = self._jobs.get(event.artifact.basename())
            job.initiators.append(event.initiator_id)

            if job.running:
                logging.debug('Worker build step already started: %s' %
                    event.artifact.basename())
                progress = WorkerBuildStepAlreadyStarted(event.initiator_id,
                    event.artifact.source.cache_key, job.who.name())
            else:
                logging.debug('Job created but not building yet '
                    '(waiting for a worker to become available): %s' %
                    event.artifact.basename())
                progress = WorkerBuildWaiting(event.initiator_id,
                    event.artifact.source.cache_key)

            self.mainloop.queue_event(WorkerConnection, progress)
        else:
            logging.debug('WBQ: Creating job for: %s' % event.artifact.name)
            job = self._jobs.create(event.artifact, event.initiator_id)

            if self._available_workers:
                self._give_job(job)
            else:
                progress = WorkerBuildWaiting(event.initiator_id,
                    event.artifact.source.cache_key)
                self.mainloop.queue_event(WorkerConnection, progress)

    def _handle_cancel(self, event_source, event):

        def cancel_this(job):
            if event.initiator_id not in job.initiators:
                return False    # not for us

            name = job.artifact.basename()
            job_id = job.id

            logging.debug('Checking whether to remove job %s with job id %s',
                          name, job_id)

            if len(job.initiators) == 1:
                if job.running or job.failed:
                    logging.debug('NOT removing running job %s with job id %s '
                                  '(WorkerConnection will cancel job)',
                                  name, job_id)
                else:
                    logging.debug('Removing job %s with job id %s',
                                  name, job_id)
                    return True
            else:
                # Don't cancel, but still remove this initiator from
                # the list of initiators
                logging.debug('NOT removing job %s with job id %s '
                              'other initiators want it: %s', name, job_id,
                              [i for i in job.initiators
                                if i != event.initiator_id])

                job.initiators.remove(event.initiator_id)

            return False

        self._jobs.remove_jobs(
            [job for (_, job) in self._jobs.get_jobs().iteritems()
                if cancel_this(job)])

    def _handle_worker(self, event_source, event):
        distbuild.crash_point()

        who = event.who
        last_job = who.job()  # the job this worker's just completed

        if last_job:
            logging.debug('%s wants new job, just did %s',
                          who.name(), last_job.artifact.basename())

            logging.debug('Removing job %s with job id %s',
                          last_job.artifact.basename(), last_job.id)
            self._jobs.remove(last_job)
        else:
            logging.debug('%s wants its first job', who.name())

        logging.debug('WBQ: Adding worker to queue: %s', event.who.name())
        self._available_workers.append(event)

        logging.debug('Current jobs: %s', self._jobs)
        logging.debug('Workers available: %d', len(self._available_workers))

        job = self._jobs.get_next_job()

        if job:
            self._give_job(job)
            
    def _give_job(self, job):
        worker = self._available_workers.pop(0)
        job.who = worker.who

        logging.debug(
            'WBQ: Giving %s to %s' %
                (job.artifact.name, worker.who.name()))

        self.mainloop.queue_event(worker.who, _HaveAJob(job))
    
    
class WorkerConnection(distbuild.StateMachine):

    '''Communicate with a single worker.'''
    
    _request_ids = distbuild.IdentifierGenerator('WorkerConnection')
    _initiator_request_map = collections.defaultdict(set)

    def __init__(self, cm, conn, writeable_cache_server, 
                 worker_cache_server_port, morph_instance):
        distbuild.StateMachine.__init__(self, 'idle')
        self._cm = cm
        self._conn = conn
        self._writeable_cache_server = writeable_cache_server
        self._worker_cache_server_port = worker_cache_server_port
        self._morph_instance = morph_instance
        self._helper_id = None
        self._job = None
        self._exec_response_msg = None
        self._debug_json = False

        addr, port = self._conn.getpeername()
        name = socket.getfqdn(addr)
        self._worker_name = '%s:%s' % (name, port)

    def name(self):
        return self._worker_name

    def job(self):
        return self._job

    def setup(self):
        distbuild.crash_point()

        logging.debug('WC: Setting up instance %s' % repr(self))
    
        self._jm = distbuild.JsonMachine(self._conn)
        self.mainloop.add_state_machine(self._jm)
        
        spec = [
            # state, source, event_class, new_state, callback
            ('idle', self._jm, distbuild.JsonEof, None,  self._reconnect),
            ('idle', self, _HaveAJob, 'building', self._start_build),
            
            ('building', distbuild.BuildController,
                distbuild.BuildCancel, 'building',
                self._maybe_cancel),

            ('building', self._jm, distbuild.JsonEof, None, self._reconnect),
            ('building', self._jm, distbuild.JsonNewMessage, 'building',
                self._handle_json_message),
            ('building', self, _BuildFailed, 'idle', self._request_job),
            ('building', self, _BuildCancelled, 'idle', self._request_job),
            ('building', self, _BuildFinished, 'caching',
                self._request_caching),

            ('caching', distbuild.HelperRouter, distbuild.HelperResult,
                'caching', self._maybe_handle_helper_result),
            ('caching', self, _Cached, 'idle', self._request_job),
            ('caching', self, _BuildFailed, 'idle', self._request_job),
        ]
        self.add_transitions(spec)
        
        self._request_job(None, None)

    def _maybe_cancel(self, event_source, build_cancel):

        if build_cancel.id not in self._job.initiators:
            return  # event not relevant

        logging.debug('WC: BuildController %r requested a cancel',
                      event_source)

        if (len(self._job.initiators) == 1):
            logging.debug('WC: Cancelling running job %s '
                          'with job id %s running on %s',
                           self._job.artifact.basename(),
                           self._job.id,
                           self.name())

            msg = distbuild.message('exec-cancel', id=self._job.id)
            self._jm.send(msg)
            self.mainloop.queue_event(self, _BuildCancelled())
        else:
            logging.debug('WC: Not cancelling running job %s with job id %s, '
                          'other initiators want it done: %s',
                          self._job.artifact.basename(),
                          self._job.id,
                          [i for i in self._job.initiators
                            if i != build_cancel.id])

        self._job.initiators.remove(build_cancel.id)

    def _reconnect(self, event_source, event):
        distbuild.crash_point()

        logging.debug('WC: Triggering reconnect')
        self.mainloop.queue_event(self._cm, distbuild.Reconnect())

    def _start_build(self, event_source, event):
        distbuild.crash_point()

        self._job = event.job
        self._helper_id = None
        self._exec_response_msg = None

        logging.debug('WC: starting build: %s for %s' %
                      (self._job.artifact.name, self._job.initiators))

        argv = [
            self._morph_instance,
            'worker-build',
            '--build-log-on-stdout',
            self._job.artifact.name,
        ]
        msg = distbuild.message('exec-request',
            id=self._job.id,
            argv=argv,
            stdin_contents=distbuild.serialise_artifact(self._job.artifact),
        )
        self._jm.send(msg)

        if self._debug_json:
            logging.debug('WC: sent to worker %s: %r'
                % (self._worker_name, msg))

        started = WorkerBuildStepStarted(self._job.initiators,
            self._job.artifact.source.cache_key, self.name())

        self.mainloop.queue_event(WorkerConnection, _JobStarted(self._job))
        self.mainloop.queue_event(WorkerConnection, started)

    def _handle_json_message(self, event_source, event):
        '''Handle JSON messages from the worker.'''

        distbuild.crash_point()

        logging.debug(
            'WC: from worker %s: %r' % (self._worker_name, event.msg))

        handlers = {
            'exec-output': self._handle_exec_output,
            'exec-response': self._handle_exec_response,
        }
        
        handler = handlers[event.msg['type']]
        handler(event.msg)

    def _handle_exec_output(self, msg):
        new = dict(msg)
        new['ids'] = self._job.initiators
        logging.debug('WC: emitting: %s', repr(new))
        self.mainloop.queue_event(
            WorkerConnection,
            WorkerBuildOutput(new, self._job.artifact.source.cache_key))

    def _handle_exec_response(self, msg):
        logging.debug('WC: finished building: %s' % self._job.artifact.name)
        logging.debug('initiators that need to know: %s'
            % self._job.initiators)

        new = dict(msg)
        new['ids'] = self._job.initiators

        if new['exit'] != 0:
            # Build failed.
            new_event = WorkerBuildFailed(new,
                                          self._job.artifact.source.cache_key)
            self.mainloop.queue_event(WorkerConnection, new_event)
            self.mainloop.queue_event(WorkerConnection, _JobFailed(self._job))
            self.mainloop.queue_event(self, _BuildFailed())
        else:
            # Build succeeded. We have more work to do: caching the result.
            self.mainloop.queue_event(self, _BuildFinished())
            self._exec_response_msg = new

    def _request_job(self, event_source, event):
        distbuild.crash_point()
        self.mainloop.queue_event(WorkerConnection, _NeedJob(self))

    def _request_caching(self, event_source, event):
        # This code should be moved into the morphlib.remoteartifactcache
        # module. It would be good to share it with morphlib.buildcommand,
        # which also wants to fetch artifacts from a remote cache.
        distbuild.crash_point()

        logging.debug('Requesting shared artifact cache to get artifacts')

        kind = self._job.artifact.source.morphology['kind']

        if kind == 'chunk':
            source_artifacts = self._job.artifact.source.artifacts

            suffixes = ['%s.%s' % (kind, name) for name in source_artifacts]
            suffixes.append('build-log')
        else:
            filename = '%s.%s' % (kind, self._job.artifact.name)
            suffixes = [filename]

            if kind == 'stratum':
                suffixes.append(filename + '.meta')
        
        suffixes = [urllib.quote(x) for x in suffixes]
        suffixes = ','.join(suffixes)

        worker_host = self._conn.getpeername()[0]
        
        url = urlparse.urljoin(
            self._writeable_cache_server, 
            '/1.0/fetch?host=%s:%d&cacheid=%s&artifacts=%s' %
                (urllib.quote(worker_host),
                 self._worker_cache_server_port,
                 urllib.quote(self._job.artifact.source.cache_key),
                 suffixes))

        msg = distbuild.message(
            'http-request', id=self._request_ids.next(), url=url,
            method='GET', body=None, headers=None)
        self._helper_id = msg['id']
        req = distbuild.HelperRequest(msg)
        self.mainloop.queue_event(distbuild.HelperRouter, req)
        
        progress = WorkerBuildCaching(self._job.initiators,
            self._job.artifact.source.cache_key)
        self.mainloop.queue_event(WorkerConnection, progress)

    def _maybe_handle_helper_result(self, event_source, event):
        if event.msg['id'] == self._helper_id:
            distbuild.crash_point()

            logging.debug('caching: event.msg: %s' % repr(event.msg))
            if event.msg['status'] == httplib.OK:
                logging.debug('Shared artifact cache population done')

                new_event = WorkerBuildFinished(
                    self._exec_response_msg,
                    self._job.artifact.source.cache_key)
                self.mainloop.queue_event(WorkerConnection, new_event)
                self.mainloop.queue_event(self, _Cached())
            else:
                logging.error(
                    'Failed to populate artifact cache: %s %s' %
                        (event.msg['status'], event.msg['body']))

                # We will attempt to remove this job twice
                # unless we mark it as failed before the BuildController
                # processes the WorkerBuildFailed event.
                #
                # The BuildController will not try to cancel jobs that have
                # been marked as failed.
                self.mainloop.queue_event(WorkerConnection,
                                          _JobFailed(self._job))

                new_event = WorkerBuildFailed(
                    self._exec_response_msg,
                    self._job.artifact.source.cache_key)
                self.mainloop.queue_event(WorkerConnection, new_event)

                self.mainloop.queue_event(self, _BuildFailed())

        self.mainloop.queue_event(WorkerConnection, _JobFinished(self._job))
