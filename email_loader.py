#! /usr/bin/env python

def strip_tags(value):
  import re
  return re.sub(r'<[^>]*?>', '', value)

def safe_append(obj, prop, value):
  obj.__dict__.setdefault(prop, []).append(value)

def parser(file):
  '''Parse a MHonArc HTML page for a message, with data in a file-like object
  |file|.  Set the properties of the |result| object according to parse results.
  '''
  def headers(lines, result):
    for line in [strip_tags(line).strip() for line in lines if ':' in line]:
      k, v = line.split(':', 1)
      k = k.strip().lower()
      v = v.strip()
      if k == 'subject':
        result['subject'] = v
      elif k == 'from':
        result['sender'] = v
      elif k == 'to':
        for to in v.split(','):
          result.setdefault('to', []).append(to.strip())
      elif k == 'cc':
        for cc in v.split(','):
          result.setdefault('cc', []).append(cc.strip())
      elif k == 'in-reply-to':
        result['in_reply_to'] = v
  def body(lines, result):
    result['body'] = '\n'.join(lines)
  def messageId(data, result):
    result['message_id'] = data.strip()
  def date(data, result):
    result['date'] = data.strip()
  def reference(data, result):
    result.setdefault('references', []).append(data.strip())
  def content_type(data, result):
    result['content_type'] = data.strip()

  markers = {
    'X-Head-of-Message': headers,
    'X-Body-of-Message': body,
    'X-Message-Id:': messageId,
    'X-Date:': date,
    'X-Reference:': reference,
    'X-Content-Type:': content_type
  }
  result = {}
  for line in file:
    line = line.strip()
    if not (line.startswith('<!--') and line.endswith('-->')):
      continue
    tag = line[len('<!--'):-len('-->')]
    if ':' in tag:
      true_tag, data = tag.split(':', 1)
      true_tag += ':'
      if true_tag in markers:
        markers[true_tag](data, result)
    elif tag in markers:
      end_tag = '<!--%s-End-->' % tag
      lines = []
      for line in file:
        line = line.strip()
        if line == end_tag:
          break
        lines.append(line)
      markers[tag](lines, result)
  return result

if __name__ == "__main__":
  from sys import argv
  for arg in argv[1:]:
    result =  parser(open(arg, 'r'))
    print
    print "=" * 80
    print arg.center(80)
    print "=" * 80
    for k, v in result.items():
      print '  %s: %s' % (k, v)
