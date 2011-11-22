# Copyright (c) 2011 Mick Thomure
# All rights reserved.
#
# Please see the file COPYING in this distribution for usage
# terms.

import itertools
import logging
import os
import time
import zmq

def SocketTypeToString(type):
  mapping = { zmq.PUSH : 'PUSH', zmq.PULL : 'PULL',
      zmq.PUB : 'PUB', zmq.SUB : 'SUB', zmq.REQ : 'REQ', zmq.REP : 'REP' }
  return mapping[type]

class ReceiverTimeoutException(Exception):
  """Indicates that a ZMQ recv() command timed out."""
  pass

class WorkerException(Exception):
  """Indicates that a worker node reported an exception while processing a
  request."""

  worker_exception = None  # the exception object thrown in the worker process

class Connect(object):
  """Describes the options needed to connect/bind a ZMQ socket to an
  end-point."""

  url = None  # (required) the URL passed to the connect() or bind() method of
              # the created socket
  type = None  # (optional) the type of socket to create
  bind = False  # (optional) whether this socket connects or binds
  options = None  # (optional) a dictionary of socket options (see setsockopt())
  pre_delay = None  # (optional) amount of time (in seconds) to wait before
                    # connecting/binding the socket
  post_delay = None  # (optional) amount of time (in seconds) to wait after
                     # connecting/binding the socket

  def __init__(self, url = None, type = None, bind = False, options = None):
    self.url, self.type, self.bind, self.options = url, type, bind, options

  def __str__(self):
    d = dict(self.__dict__.items())
    if d['type'] != None:
      d['type'] = SocketTypeToString(d['type'])
    keys = filter((lambda k: d[k] != None), d.keys())
    values = map(self.__getattribute__, keys)
    return "Connect(%s)" % ", ".join("%s=%s" % x for x in zip(keys, values))

  def __repr__(self):
    return str(self)

  def __call__(self, context, url = None, type = None, bind = None,
      options = None):
    return self.MakeSocket(context, url, type, bind, options)

  def MakeSocket(self, context, url = None, type = None, bind = None,
      options = None, pre_delay = None, post_delay = None):
    """Create the socket. Arguments take precendence over their corresponding
    object attributes."""
    if type == None:
      type = self.type
    if url == None:
      url = self.url
    if bind == None:
      bind = self.bind
    if options == None:
      options = {}
    if self.options != None:
      if options == None:
        options = self.options
      else:
        options = dict(options.items() + self.options.items())
    if pre_delay == None:
      pre_delay = self.pre_delay
    if post_delay == None:
      post_delay = self.post_delay
    socket = context.socket(type)
    for k, v in options.items():
      socket.setsockopt(k, v)
    if pre_delay != None:
      time.sleep(pre_delay)
    if bind:
      logging.info("Binding %s socket to %s with context %s" % (
          SocketTypeToString(type), url, hash(context)))
      socket.bind(url)
    else:
      logging.info("Connecting %s socket to %s with context %s" % (
          SocketTypeToString(type), url, hash(context)))
      socket.connect(url)
    if post_delay != None:
      time.sleep(post_delay)
    return socket

class BasicVentilator(object):
  """Push tasks to worker nodes on the cluster."""

  request_sender = None  # (required) constructor for the writer socket

  def __init__(self, context, request_sender, worker_connect_delay = None):
    """
    worker_connect_delay -- (float) length of time to wait (in seconds) between
                            calling Setup() and starting to transmit tasks to
                            the cluster. This gives worker nodes a chance to
                            connect, avoiding ZMQ's "late joiner syndrome".
                            Default delay is one second.
    """
    if worker_connect_delay == None:
      worker_connect_delay = 1.
    self.request_sender = request_sender
    self._ready = False
    self._start = None
    self.context = context
    self.worker_connect_delay = worker_connect_delay

  def Setup(self):
    logging.info("Ventilator: starting ventilator on pid %d" % os.getpid())
    #~ logging.info("Ventilator:   sender: %s" % self.request_sender.url)
    if isinstance(self.request_sender, Connect):
      self._sender = self.request_sender.MakeSocket(self.context, type = zmq.PUSH)
    else:
      self._sender = self.request_sender
    self._connect_delay = time.time() + self.worker_connect_delay
    logging.info("Ventilator: bound, starting at pid %d" % os.getpid())
    self._ready = True

  def Shutdown(self):
    del self._sender
    self._ready = False

  def Send(self, requests):
    """Reads tasks from an iterator, and sends them to worker nodes.
    RETURN the number of sent tasks.
    """
    # Set up the connection
    if not self._ready:
      self.Setup()
    # Give worker nodes time to connect
    time_delta = time.time() - self._connect_delay
    if time_delta > 0:
      time.sleep(time_delta)
    logging.info("Ventilator: starting send")
    num_requests = 0
    for request in requests:
      logging.info("Ventilator: sending task %d" % num_requests)
      self._sender.send_pyobj(request)
      logging.info("Ventilator: finished sending task %s" % num_requests)
      num_requests += 1
    logging.info("Ventilator: finished sending %d tasks" % num_requests)
    return num_requests

class BasicSink(object):
  """Collect results from worker nodes on the cluster."""

  result_receiver = None  # (required) constructor for the reader socket
  command_receiver = None  # (optional) constructor for the command socket

  CMD_KILL = "CLUSTER_SINK_KILL"  # Send this to the command socket to shut down
                                  # the sink.

  def __init__(self, context, result_receiver, command_receiver = None,
      receive_timeout = None):
    """Create a new Sink object.
    result_receiver -- (zmq.socket or Connect) channel on which to receive
                       results
    command_receiver -- (zmq.socket or Connect, optional) channel on which to
                        receive quit command
    """
    self.context = context
    self.result_receiver = result_receiver
    self.command_receiver = command_receiver
    self.receive_timeout = receive_timeout
    self._ready = False

  def Setup(self):
    self._poller = zmq.Poller()
    if isinstance(self.result_receiver, Connect):
      self._receiver_socket = self.result_receiver.MakeSocket(self.context,
          type = zmq.PULL)
    else:
      self._receiver_socket = self.result_receiver
    self._poller.register(self._receiver_socket)
    self._command_socket = None
    logging.info("BasicSink: starting sink on pid %d" % os.getpid())
    logging.info("BasicSink:   reader: %s" % self.result_receiver)
    logging.info("BasicSink:   command: %s" % self.command_receiver)
    if self.command_receiver != None:
      if isinstance(self.command_receiver, Connect):
        self._command_socket = self.command_receiver.MakeSocket(self.context,
            type = zmq.SUB)
      else:
        self._command_socket = self.command_receiver
      self._poller.register(self._command_socket)
    logging.info("BasicSink: bound at pid %d" % os.getpid())
    self._ready = True
    logging.info("BasicSink: setup done")

  def Shutdown(self):
    del self._poller
    del self._receiver_socket
    del self._command_socket

  def Receive(self, num_results = None, timeout = None):
    """Returns an iterator over results, available as they arrive at the sink.
    Raises a ReceiverTimeoutException if no result arrives in the given time
    period.
    num_results -- (int) return after a fixed number of results have arrived
    timeout -- (int) time to wait for each result (in milliseconds)
    """
    if not self._ready:
      self.Setup()
    idx = 0
    if timeout == None:
      timeout = self.receive_timeout
    while True:
      if num_results != None and idx >= num_results:
        break
      #~ logging.info("BasicSink: polling with timeout %s" % timeout)
      socks = dict(self._poller.poll(timeout))
      #~ logging.info("BasicSink: poll finished with socks = %s" % (socks,))
      if len(socks) == 0:
        raise ReceiverTimeoutException
      if self._receiver_socket in socks:
        result = self._receiver_socket.recv_pyobj()
        logging.info("BasicSink: received object: %s" % result)
        yield result
        idx += 1
      if self._command_socket in socks:
        cmd = self._command_socket.recv_pyobj()
        logging.info("BasicSink: got command %s on pid %d" % (cmd, os.getpid()))
        if cmd == self.CMD_KILL:
          logging.info("BasicSink: received quit command")
          break
        # Ignore unrecognized commands.
    raise StopIteration

  @staticmethod
  def SendKillCommand(context, command_sender):
    """
    command_sender -- (zmq.socket or Connect)
    """
    #~ logging.info("Sink:   command: %s" % command_sender.url)
    if isinstance(command_sender, Connect):
      commands = command_sender.MakeSocket(context, type = zmq.PUB)
    else:
      commands = command_sender
    time.sleep(1)  # wait for sink process/thread to connect
    logging.info("BasicSink.SendKillCommand: sending kill command")
    commands.send_pyobj(BasicSink.CMD_KILL)
    logging.info("BasicSink.SendKillCommand: kill command sent")

class ClusterResult(object):
  """A cluster result, corresponding to the output value of a callback when
  applied to one input element."""

  status = None  # whether the input elements were processed successfully
  payload = None  # output corresponding to task's input elements. this will
                  # either be a list -- in the case of a map() operation -- or a
                  # scalar -- in the case of a reduce().
  exception = None  # exception that occurrred during processing, if any

  STATUS_SUCCESS = "OK"  # indicates that request was processed successfully
  STATUS_FAIL = "FAIL"  # indicates that error occurred while processing request

  def __init__(self, status = None, payload = None, exception = None):
    self.status, self.payload, self.exception = status, payload, exception

Ventilator = BasicVentilator

class Sink(BasicSink):

  def Receive(self, num_results = None, timeout = None):
    results = super(Sink, self).Receive(num_results, timeout)
    for result in results:
      if result.status != ClusterResult.STATUS_SUCCESS:
        raise WorkerException("Caught exception in worker node: %s" % \
            result.exception)
      yield result.payload
    raise StopIteration

class Worker(object):

  # TODO: figure out how to make cluster worker run multiple threads/procs?

  CMD_KILL = "CLUSTER_WORKER_KILL"  # Send this to the command socket to shut
                                    # down the worker.

  def __init__(self, context, request_receiver, result_sender, callback,
      command_receiver = None, receiver_timeout = None):
    """Handles requests that arrive on a socket, writing results to another
    socket.
    request_receiver -- (Connect) channel for receiving incoming requests
    result_sender -- (Connect) channel for sending results
    callback -- (callable) function to convert a request to a result
    command_receiver -- (zmq.socket or Connect) channel for receiving kill
                        commands
    context -- (zmq.Context) context used to create sockets. set for threaded
               workers only.
    """
    self.context, self.request_receiver, self.result_sender, self.callback, \
        self.command_receiver, self.receiver_timeout = context, \
        request_receiver, result_sender, callback, command_receiver, \
        receiver_timeout
    self.receiver = None

  def Setup(self):
    logging.info("Worker: starting worker on pid %s" % os.getpid())
    logging.info("Worker:   receiver: %s" % self.request_receiver)
    logging.info("Worker:   command: %s" % self.command_receiver)
    logging.info("Worker:   sender: %s" % self.result_sender)
    # Set up the sockets
    self.receiver = self.request_receiver.MakeSocket(self.context,
        type = zmq.PULL)
    self.sender = self.result_sender.MakeSocket(self.context, type = zmq.PUSH)
    self.poller = zmq.Poller()
    self.poller.register(self.receiver, zmq.POLLIN)
    if self.command_receiver != None:
      self.cmd_subscriber = self.command_receiver.MakeSocket(self.context,
          type = zmq.SUB, options = {zmq.SUBSCRIBE : ""})
      self.poller.register(self.cmd_subscriber, zmq.POLLIN)
    else:
      self.cmd_subscriber = None
    logging.info("Worker: bound at pid %d" % os.getpid())

  def Run(self):
    if self.receiver == None:
      self.Setup()
    # Handle incoming requests, and watch for KILL commands
    while True:
      socks = dict(self.poller.poll(self.receiver_timeout))
      if len(socks) == 0:
        raise ReceiverTimeoutException
      if self.receiver in socks:
        request = self.receiver.recv_pyobj()
        result = ClusterResult()
        try:
          # Apply user callback to the request
          result.payload = self.callback(request)
          result.status = ClusterResult.STATUS_SUCCESS
        except Exception, e:
          logging.info(("Worker: caught exception %s from request " % e) + \
              "processor")
          result.exception = e
          result.status = ClusterResult.STATUS_FAIL
        self.sender.send_pyobj(result)
      if self.cmd_subscriber in socks:
        cmd = self.cmd_subscriber.recv_pyobj()
        logging.info("Worker: got cmd %s on pid %d" % (cmd, os.getpid()))
        if cmd == Worker.CMD_KILL:
          logging.info("Worker: quiting on pid %d" % os.getpid())
          break

  @staticmethod
  def SendKillCommand(context, command_sender):
    """Send a kill command to all workers on a given channel.
    command_sender -- (zmq.socket or Connect)
    """
    logging.info("Worker: sending kill command")
    logging.info("Worker:   command: %s" % command_sender)
    if isinstance(command_sender, Connect):
      commands = command_sender.MakeSocket(context, type = zmq.PUB)
    else:
      commands = command_sender
    time.sleep(1)  # wait for workers to connect
    commands.send_pyobj(Worker.CMD_KILL)

def LaunchStreamerDevice(context, frontend_connect, backend_connect):
  frontend = frontend_connect.MakeSocket(context, type = zmq.PULL, bind = True)
  backend = backend_connect.MakeSocket(context, type = zmq.PUSH, bind = True)
  logging.info("LaunchStreamerDevice: starting streamer on pid %d" % \
      os.getpid())
  logging.info("LaunchStreamerDevice:   frontend:", frontend_connect.url)
  logging.info("LaunchStreamerDevice:   backend:", backend_connect.url)
  zmq.device(zmq.STREAMER, frontend, backend)

def LaunchForwarderDevice(context, frontend_connect, backend_connect):
  frontend = frontend_connect.MakeSocket(context, type = zmq.SUB, bind = False,
      options = {zmq.SUBSCRIBE : ""})
  backend = backend_connect.MakeSocket(context, type = zmq.PUB, bind = True)
  logging.info("LaunchForwarderDevice: starting forwarder on pid %d" % \
      os.getpid())
  logging.info("LaunchForwarderDevice:   frontend:", frontend_connect.url)
  logging.info("LaunchForwarderDevice:   backend:", backend_connect.url)
  zmq.device(zmq.FORWARDER, frontend, backend)
