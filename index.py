import logging
from google.appengine.api import users, urlfetch
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.api.labs.taskqueue import Task
from models import Message, List, Thread, UserThread
from email_loader import strip_tags
from datetime import datetime, timedelta

from StringIO import StringIO
import re

def gql_limit1(model,  **conditions):
  query = "WHERE " + " AND ".join("%s = :%s" % (k, k) for k in conditions)
  cursor = model.gql(query, **conditions)
  for result in cursor.fetch(1):
    return result
  return None

def get_list_from_url(list_url):
  list = gql_limit1(List, list_url = list_url)
  if list is None:
      if re.match(r'^http(s?)://.*/msg(\d{5}).html$', list_url):
        list_url = list_url[:-len('/msg00000.html')]
        list = gql_limit1(List, list_url = list_url)
  return (list, list_url)

def update_list(list, start_msg, limit = 10):
  import email_loader
  import urllib
  required_fields = ['date', 'message_id', 'subject', 'body', 'sender']
  thread_id_cache = {}
  thread_pool = {}
  message_pool = []
  new = 0
  err = None
  for i in range(start_msg, start_msg + limit):
    url = "%s/msg%05i.html" % (list.list_url, i)
    logging.info("loading an email from url %s" % url)

    # Try three times to fetch the URL
    result = None
    for attempt in range(3):
      try:
        result = urlfetch.fetch(url=url)
        err = None
        break
      except urlfetch.DownloadError, msg:
        err = msg
        continue
    if result is None: break

    if result.status_code == 404:
      # This message does not exist, so return messages collected so far.
      break
    elif result.status_code != 200:
      err = 'Got status code %i while trying to fetch %s' % (result.status_code,
                                                             url)
      break

    logging.info("got content: %s" % result.content)
    result = email_loader.parser(StringIO(result.content))

    logging.info("got result: %s" % result)
    result['source_url'] = url
    result['list_msg_id'] = i
    if not all((field in result) for field in required_fields):
      logging.error(
        "failed to update list %s msg %i with url %s; got bad parse result %s"
        % (list, i, url, result))
      break
    result['date'] = datetime.strptime(' '.join(result['date'].split()[:-1]),
                                       '%a, %d %b %Y %H:%M:%S')
    # Determine thread_id
    if 'references' in result:
      for reference in result['references']:
        if 'thread_id' in result:
          break
        if reference in thread_id_cache:
          result['thread_id'] = thread_id_cache[reference]

      for reference in result['references']:
        if 'thread_id' in result:
          break
        ref = gql_limit1(Message, message_id = reference)
        if ref is not None:
          result['thread_id'] = ref.thread_id
    # If no thread_id was found, start a new thread with this message
    if 'thread_id' not in result:
      result['thread_id'] = result['message_id']
      thread = Thread(thread_id = result['message_id'],
                      list_url = list.list_url,
                      last_message_time = result['date'],
                      subject = result['subject'],
                      last_message_body = result['body'],
                      participants = [result['sender']])
      thread_pool[result['thread_id']] = thread

    # Build the message object
    message = Message(**result)

    # Update the thread
    if result['thread_id'] not in thread_pool:
      thread = gql_limit1(Thread, thread_id = result['thread_id'])
      thread_pool[result['thread_id']] = thread
    thread = thread_pool[result['thread_id']]
    thread.last_message_time = message.date
    thread.last_message_body = message.body
    if message.sender not in thread.participants:
      thread.participants.append(message.sender)
    thread_id_cache[result['message_id']] = result['thread_id']

    # Update the message_pool
    message_pool.append(message)
    new += 1

  for message in message_pool:
    message.put()
  for thread_id, thread in thread_pool.items():
    thread.put()
  if new > 0:
    list.num_fetched_msg += new
    list.last_fetched_time = datetime.now()
    list.put()
  return new, err

def schedule_list_update(list):
  Task(url = '/list/update', params = {'list' : list.list_url},
       countdown = 1).add()

def render(rh, path, **kw):
  kw['user'] = users.get_current_user()
  if kw['user']:
    kw['logout_url'] = users.create_login_url(rh.request.uri)
  else:
    kw['login_url'] = users.create_login_url(rh.request.uri)
  rh.response.out.write(template.render(path, kw))


class MainPage(webapp.RequestHandler):
  def get(self):
    lists = List.all().order('-last_fetched_time')
    render(self, 'index.html', lists = lists)


class ShowList(webapp.RequestHandler):
  def get(self):
    list, list_url = get_list_from_url(self.request.get('list'))
    msg = None
    if list is None:
      render(self, 'error.html', msg = 'The specified list does not exist.')
      return
    # If the last time the list was updated was over 12 hours ago, schedule an
    # update
    if list.last_fetched_time < datetime.now() - timedelta(hours=12):
      schedule_list_update(list)
      msg = 'Fetching new messages...'

    threads = Thread.all().filter('list_url =', list_url).\
                     order('-last_message_time')
    formatted_threads = []
    for t in threads:
      if len(t.participants) > 3:
        t.participants = [t.participants[0], '...'] + t.participants[-2:]
      t.participants = [', '.join(p.split()[0] for p in  t.participants)]
      t.subject = strip_tags(t.subject)
      t.last_message_body = strip_tags(t.last_message_body)[:100]
      one_day = timedelta(days=1)
      if t.last_message_time > datetime.now() - timedelta(days=1):
        t.short_date = datetime.strftime(t.last_message_time, '%H:%M%p')
      elif t.last_message_time > datetime.now() - timedelta(days=365):
        t.short_date = datetime.strftime(t.last_message_time, '%b %d')
      else:
        t.short_date = datetime.strftime(t.last_message_time, '%D')
      t.read = False
      formatted_threads.append(t)

    user = users.get_current_user()
    if user:
      def lookup():
        if len(lookup_pool) == 0:
          return
        for ut in UserThread.all().filter('thread_id IN', lookup_pool):
          last_viewed[ut.thread_id] = ut.last_viewed
        del lookup_pool[:]

      last_viewed = {} # maps from thread_ids to last viewed
      lookup_pool = []
      for t in formatted_threads:
        lookup_pool.append(t.thread_id)
        if len(lookup_pool) == 30:
          lookup()
      lookup()

      for t in formatted_threads:
        if t.thread_id in last_viewed:
          if t.last_message_time <= last_viewed[t.thread_id]:
            t.read = True

    render(self, 'view.html', list = list, threads = formatted_threads,
                              msg = msg)


class CreateList(webapp.RequestHandler):
  def post(self):
    list, list_url = get_list_from_url(self.request.get('list'))

    if list is not None:
      render(self, 'error.html', msg = 'That list already exists!')
      return

    if not list_url:
      render(self, 'error.html', msg = 'No url given.')
      return

    list = List(list_url = list_url, num_fetched_msg = 0,
                last_fetched_time = datetime.now())

    fetched, err = update_list(list, 0, 1)
    if err:
      render(self, 'info.html',
             error_message =  "Error fetching messages: " + str(err))
    elif fetched == 0:
      err_msg = "Unable to get any messages from url \"%s\"" % list_url
      render(self, 'info.html', error_message = err_msg)
    else:
      schedule_list_update(list)
      render(self, 'info.html',
             message='List added; messages are being fetched.')
    

class UpdateList(webapp.RequestHandler):
  def do(self):
    list = gql_limit1(List, list_url = self.request.get('list'))
    try:
      n = int(self.request.get('n', 1))
    except:
      n = 1
    if not list:
      render(self, 'info.html', error_message='unknown list')
      return

    fetched, error = update_list(list, list.num_fetched_msg, limit = n)
    if error:
      render(self, 'info.html', error_message='Error: ' + str(error))
    elif fetched > 0:
      render(self, 'info.html',
             message='%i message(s) added, update scheduled' % fetched)
      schedule_list_update(list)
    else:
      render(self, 'info.html', message='no messages added')

  def post(self):
    self.do()

  def get(self):
    self.do()
    

class ShowThread(webapp.RequestHandler):
  def get(self):
    thread_id = self.request.get('thread')
    thread = gql_limit1(Thread, thread_id = thread_id)
    messages = Message.all().filter('thread_id = ', thread_id)

    formatted_messages = []
    for message in messages:
      if message.content_type == 'text/plain':
        message.body = strip_tags(message.body).strip().\
                                                replace(' ', '&nbsp;').\
                                                replace('\n\n', '</p><p>').\
                                                replace('\n', '<br>')
        message.body = '<p>' + message.body + '</p>'
      formatted_messages.append(message)
    
    if len(formatted_messages) == 0:
      render(self, 'info.html', error_message = "No messages in this thread.")
      return

    user = users.get_current_user()
    already_seen = None
    if user:
      # If user is logged in, mark these messages as viewed.
      max_viewed = max(msg.date for msg in formatted_messages)
      userthread = gql_limit1(UserThread, thread_id = thread_id, user = user)
      if not userthread:
        userthread = UserThread(user = user, thread_id = thread_id,
                                last_viewed = max_viewed)
      else:
        already_seen = userthread.last_viewed
        for message in formatted_messages:
          if message.date <= already_seen:
            message.read = True
      userthread.last_viewed = max_viewed
      userthread.put()


    render(self, 'thread.html', thread=thread, already_seen=already_seen,
           messages=formatted_messages)

application = webapp.WSGIApplication([
                                      ('/', MainPage),
                                      ('/list/create', CreateList),
                                      ('/list/show', ShowList),
                                      ('/list/update', UpdateList),
                                      ('/thread/show', ShowThread),
                                     ],
                                     debug=True)

def main():
  run_wsgi_app(application)

if __name__ == "__main__":
  main()
