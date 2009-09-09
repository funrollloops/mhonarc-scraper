from google.appengine.api import users
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from models import Message, List, Thread
from email_loader import strip_tags
from datetime import datetime, timedelta
import re

def is_logged_in(request_handler):
  user = users.get_current_user()
  if user:
    request_handler.user = user
    return true
  else:
    request_handler.redirect(users.create_login_url(request_handler.request.uri))

def gql_limit1(model,  **conditions):
  query = "WHERE " + " AND ".join("%s = :%s" % (k, k) for k in conditions)
  cursor = model.gql(query, **conditions)
  for result in cursor.fetch(1):
    return result
  return None

def update_list(list_url, start_msg, limit = 10):
  import email_loader
  import urllib
  thread_id_cache = {}
  thread_pool = {}
  message_pool = []
  new = 0
  for i in range(start_msg, start_msg + limit):
    url = "%s/msg%05i.html" % (list_url, i)
    result = email_loader.parser(urllib.urlopen(url))
    result['source_url'] = url
    result['list_msg_id'] = i
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
                      list_url = list_url,
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
  return new

def render(rh, path, **kw):
  rh.response.out.write(template.render(path, kw))

class MainPage(webapp.RequestHandler):
  def get(self):
    user = users.get_current_user()
    if user:
      self.response.headers['Content-Type'] = 'text/plain'
      self.response.out.write('Hello, ' + user.nickname())
    else:
      self.redirect(users.create_login_url(self.request.uri))
    
class ShowList(webapp.RequestHandler):
  def get(self):
    list_url = self.request.get('list')
    # Check to see that URL is of the right form.

    list = gql_limit1(List, list_url = list_url)
    if list is None:
      if not re.match(r'^http(s?)://.*/msg(\d{5}).html$', list_url):
        render(self, 'error.html',
               msg = "Given url does not end with msgXXXXX.html")
        return
      list_url = list_url[:-len('/msg00000.html')]
      list = gql_limit1(List, list_url = list_url)

    if list is None:
      new_msgs = update_list(list_url, 0)
      if new_msgs > 0:
        list = List(list_url = list_url, num_fetched_msg = new_msgs,
                    last_fetched_time = datetime.now())
        list.put()
      else:
        render(self, 'error.html',
               msg = "Unable to get any messages from url \"%s\"" % list_url)
        return

    threads = Thread.all().filter('list_url =', list_url).\
                     order('-last_message_time')
    formatted_threads = []
    for t in threads:
      def format_participant(p):
        ps = p.split()
        if len(ps) > 1:
          return ps[0] + " " + ps[1][0]
        else:
          return ps[0]

      t.participants = [', '.join(map(format_participant, t.participants))]
      t.subject = strip_tags(t.subject)
      t.last_message_body = strip_tags(t.last_message_body)[:100]
      one_day = timedelta(days=1)
      if t.last_message_time > datetime.now() - timedelta(days=1):
        t.short_date = datetime.strftime(t.last_message_time, '%H:%M%p')
      elif t.last_message_time > datetime.now() - timedelta(days=365):
        t.short_date = datetime.strftime(t.last_message_time, '%b %d')
      else:
        t.short_date = datetime.strftime(t.last_message_time, '%D')
      formatted_threads.append(t)

    render(self, 'view.html', list_url = list_url, threads = formatted_threads)
    
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
      render(self, 'error.html', msg = "No messages in this thread.")
    else:
      render(self, 'thread.html', thread=thread, messages=formatted_messages)

application = webapp.WSGIApplication([
                                      ('/', MainPage),
                                      ('/list', ShowList),
                                      ('/thread', ShowThread)
                                     ],
                                     debug=True)

def main():
  run_wsgi_app(application)

if __name__ == "__main__":
  main()
