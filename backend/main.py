import datetime
import itertools
import json
import logging

from google.appengine.api import mail
from google.appengine.api import memcache
import stripe
import webapp2

import env
import handlers
import model
import templates
import util
import wp_import

class Error(Exception): pass


# TODO(hjfreyer): Tests!!
class ContactHandler(webapp2.RequestHandler):
  def post(self):
    data = json.loads(self.request.body)
    ascii_name = data["name"].encode('ascii', errors='ignore')
    ascii_email = data["email"].encode('ascii', errors='ignore')
    ascii_subject = data["subject"].encode('ascii', errors='ignore')
    ascii_body = data["body"].encode('ascii', errors='ignore')

    replyto = '%s <%s>' % (ascii_name, ascii_email)
    message = mail.EmailMessage(sender=('MayOne no-reply <noreply@%s.appspotmail.com>' %
                                           model.Config.get().app_name),
                                reply_to=replyto,
                                subject=ascii_subject)
    message.to = "info@mayone.us"
    message.body = 'FROM: %s\n\n%s' % (ascii_email, ascii_body)
    message.send()
    util.EnableCors(self)
    self.response.write('Ok.')

  def options(self):
    util.EnableCors(self)


# DEPRECATED: Remove in favor of handlers.TotalHandler
class GetTotalHandler(webapp2.RequestHandler):
  def get(self):
    team = self.request.get("team")
    if team:
      return self.getTeamTotal(team)
    total = (handlers.TotalHandler.PRE_SHARDING_TOTAL +
             handlers.TotalHandler.WP_PLEDGE_TOTAL +
             handlers.TotalHandler.DEMOCRACY_DOT_COM_BALANCE +
             handlers.TotalHandler.CHECKS_BALANCE)
    total += model.ShardedCounter.get_count('TOTAL-5')
    total = int(total/100) * 100
    self.response.headers['Content-Type'] = 'application/javascript'
    self.response.write('%s(%d)' % (self.request.get('callback'), total))

  def getTeamTotal(self, team):
    self.response.headers['Content-Type'] = 'application/javascript'
    key = "TEAM-TOTAL-%s" % team
    res = memcache.get(key)
    if not res:
      total_pledges, total_amount = 0, 0
      for pledge in model.Pledge.all().filter("team =", team):
        total_pledges += 1
        total_amount += pledge.amountCents
      # doh, we should probably return a json object here instead of just an
      # some ints, but we'd like to be backwards compatible with the previous
      # (non-team) api. so for now, let's make use javascript varargs
      res = '(%d, %d)' % (total_amount, total_pledges)
      memcache.add(key, res, 60)
    self.response.write("%s%s" % (self.request.get('callback'), res))


class EmbedHandler(webapp2.RequestHandler):
  def get(self):
    if self.request.get('widget') == '1':
      self.redirect('/embed.html')
    else:
      self.redirect('/')


class UserUpdateHandler(webapp2.RequestHandler):
  def get(self, url_nonce):
    user = model.User.all().filter('url_nonce =', url_nonce).get()
    if user is None:
      self.error(404)
      self.response.write('This page was not found')
      return

    template = templates.GetTemplate('user-update.html')
    self.response.write(template.render({'user': user}))

  def post(self, url_nonce):
    try:
      user = model.User.all().filter('url_nonce =', url_nonce).get()
      if user is None:
        self.error(404)
        self.response.write('This page was not found')
        return

      user.occupation = self.request.get('occupation')
      user.employer = self.request.get('employer')
      user.phone = self.request.get('phone')
      user.target = self.request.get('target')
      user.put()
      template = templates.GetTemplate('user-update.html')
      ctx = {'user': user, 'success': True}
      self.response.write(template.render(ctx))
    except:
      self.error(400)
      self.response.write('There was a problem submitting the form')
      return


class UserInfoHandler(webapp2.RequestHandler):
  def get(self, url_nonce):
    util.EnableCors(self)
    user = model.User.all().filter('url_nonce =', url_nonce).get()
    if user is None:
      self.error(404)
      self.response.write('user not found')
      return

    # maybe we should do sum instead?
    biggest_pledge = None
    biggest_amount = 0
    for pledge in itertools.chain(
        model.Pledge.all().filter('email =', user.email),
        model.WpPledge.all().filter('email =', user.email)):
      if (pledge.amountCents or 0) >= biggest_amount:
        biggest_pledge = pledge
        biggest_amount = (pledge.amountCents or 0)

    if biggest_pledge is None:
      self.error(404)
      self.response.write("user not found")
      return

    stripe.api_key = env.get_env().stripe_backend.stripe_private_key
    cus = stripe.Customer.retrieve(biggest_pledge.stripeCustomer)
    if len(cus.cards.data) == 0:
      self.error(404)
      self.response.write("user not found")
      return

    if user.first_name or user.last_name:
      # TODO(jt): we should backfill this information
      user_name = "%s %s" % (user.first_name or "", user.last_name or "")
    else:
      user_name = cus.cards.data[0].name

    zip_code = cus.cards.data[0].address_zip

    self.response.headers['Content-Type'] = 'application/javascript'
    self.response.write(json.dumps({
        "user": {
          "name": user_name,
          "email": user.email,
          "pledge_amount_cents": biggest_amount,
          "zip_code": zip_code}}))

  def options(self):
    util.EnableCors(self)


app = webapp2.WSGIApplication([
  ('/total', GetTotalHandler),
  (r'/user-update/(\w+)', UserUpdateHandler),
  (r'/user-info/(\w+)', UserInfoHandler),
  ('/campaigns/may-one/?', EmbedHandler),
  ('/contact.do', ContactHandler),
  # See wp_import
  # ('/import.do', wp_import.ImportHandler),
] + handlers.HANDLERS, debug=False, config=dict(env=env.get_env()))
