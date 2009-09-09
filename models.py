from google.appengine.ext import db

class Message(db.Expando):
  message_id = db.StringProperty(required=True)
  thread_id = db.StringProperty(required=True)
  list_msg_id = db.IntegerProperty(required=True)
  source_url = db.LinkProperty(required=True)

  sender = db.StringProperty(required=True)
  to = db.StringListProperty()
  cc = db.StringListProperty()
  date = db.DateTimeProperty(required=True)
  in_reply_to = db.StringProperty()
  body = db.TextProperty(required=True)
  content_type = db.StringProperty(default="text/plain")

class List(db.Model):
  list_url = db.LinkProperty(required=True) # primary key
  num_fetched_msg = db.IntegerProperty()
  last_fetched_time = db.DateTimeProperty()

class Thread(db.Model):
  thread_id = db.StringProperty(required=True)
  list_url = db.LinkProperty(required=True)
  last_message_time = db.DateTimeProperty(required=True)
  subject = db.StringProperty(required=True)
  last_message_body = db.TextProperty()
  participants = db.StringListProperty()
