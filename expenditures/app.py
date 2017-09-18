# -*- coding: utf-8 -*-

from flask import Flask, jsonify
from flask_cors import CORS, cross_origin
from redis import Redis
import firebase_admin
from firebase_admin import credentials, db


import collections
import datetime as dt
import json
import random
import requests
import os

app = Flask(__name__)
redis = Redis(host='redis', port=6379)
cred = credentials.Certificate('/code/firebaseServiceAccountKey.json')
firebase_app = firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://illinois-calc.firebaseio.com/'
    })

candidates = [
    {
        'id': 'rauner',
        'name': u'Bruce Rauner',
        'party': 'r',
        'committeeId': 25185
    },
    {
        'id': 'biss',
        'name': u'Daniel Biss',
        'party': 'd',
        'committeeId': 23971
    },
    {
        'id': 'daiber',
        'name': u'Bob Daiber',
        'party': 'd',
        'committeeId': 32591
    },
    {
        'id': 'drury',
        'name': u'Scott Drury',
        'party': 'd',
        'committeeId': 23682
    },
    {
        'id': 'hardiman',
        'name': u'Tio Hardiman',
        'party': 'd',
        'committeeId': ''
   },
    {
        'id': 'kennedy',
        'name': u'Chris Kennedy',
        'party': 'd',
        'committeeId': 32590
    },
    {
        'id': 'paterakis',
        'name': u'Alex Paterakis',
        'party': 'd',
        'committeeId': 32289
    },
    {
        'id': 'pawar',
        'name': u'Ameya Pawar',
        'party': 'd',
        'committeeId': 32469
    },
    {
        'id': 'biss',
        'name': u'Daniel Biss',
        'party': 'd',
        'committeeId': 23971
    },
    {
        'id': 'pritzker',
        'name': u'J.B. Pritzker',
        'party': 'd',
        'committeeId': 32762
    },
]

day_to_other_time = collections.OrderedDict(
        [('second', (1/(24*60*60))),
        ('minute', (1/(24*60))),
        ('hour', (1/24)),
        ('day', 1),
        ('week', 7),
        ('month', 30)]
        )

# how long our cached items last
redisDuration = 3600 # one hour
# assume there will never be more than 1,000,000 expenditures (but you never know, amirite?)
apiLimit = 1000000
dateFormat = '%Y-%m-%dT%H:%M:%S'

def calculateSpendingDays(firstExpenditure):
    now = dt.datetime.now()
    then = dt.datetime.strptime(firstExpenditure, dateFormat)

    diff = now - then

    return diff.days


def calculateSpentPerDay(days, total):
    return total / days


def calculateSpentPerSecond(perDay):
    return perDay / 86400


def getBestTimespanForSpend(spentPerDay, fact_amount):
    for timespan, ratio in day_to_other_time.iteritems():
        spentPerTime = spentPerDay * ratio
        if spentPerTime >= fact_amount:
            return timespan
    return None


# convenience during development/testing
@app.route('/clear', methods=['GET'])
def clear():
    for c in candidates:
        redis.delete(c.get('id'))

    return 'cache cleared'

@app.route('/expenditures/facts/random', methods=['GET'])
@cross_origin()
def get_random_fact():
    global app, candidates, day_to_other_time
    # pick a random fact from the db
    # pick a random candidate and get their numbers
    # calculate stuff and return the text
    facts_ref = db.reference('facts')
    lastfact = facts_ref.order_by_key().limit_to_last(1).get()
    for key in lastfact:
        max_fact_id = key
    rand_fact_id = random.randrange(1, int(max_fact_id))

    fact_ref = db.reference('facts/%d'%rand_fact_id)
    rand_fact = fact_ref.get()

    rand_cand = random.choice(candidates)
    cand_expenditures = get_cand_expenditures(rand_cand['id'])
    app.logger.info(str(cand_expenditures))

    # get it before rounding
    spentPerDay = calculateSpentPerDay(float(cand_expenditures['spendingDays']),
            float(cand_expenditures['total']))

    timespan = getBestTimespanForSpend(spentPerDay, float(rand_fact['amount']))
    if timespan is None:
        # seems like this shouldn't happen if we're picking the right facts, but may need to find
        # a better thing to do here
        text = 'We couldn\'t find a good timespan for this fact and candidate'
    else:
        text = '%s spends more in one %s than %s. Source: %s'%(
                rand_cand['name'],
                timespan,
                rand_fact['item'],
                rand_fact['source'])
    resp = {'text':text}
    return jsonify(resp)






def get_cand_expenditures(candidate_nick):
    # find a matching committee_id
    committeeId = None

    # default to error message
    responseJSON = { 'error': 'Candidate not found' }

    for c in candidates:
        if c.get('id') == candidate_nick:
            committeeId = c.get('committeeId')
            break

    if committeeId:
        # try to pull data from redis
        cachedJSON = redis.get(candidate_nick)

        # if data found in redis, use it
        if cachedJSON:
            responseJSON = json.loads(cachedJSON)
        # if data not found in redis:
        else:
            # make API call
            response = requests.get('https://www.illinoissunshine.org/api/expenditures/?limit={}&committee_id={}'.format(apiLimit, committeeId))

            apiData = json.loads(json.dumps(response.json()))

            total = 0.0

            for expenditure in apiData['objects'][0]['expenditures']:
                total = total + float(expenditure['amount'])

            firstExpenditure = apiData['objects'][0]['expenditures'][-1]['expended_date']
            spendingDays = calculateSpendingDays(firstExpenditure)
            spentPerDay = calculateSpentPerDay(spendingDays, total)

            responseJSON = {
                'total': "{0:.2f}".format(total),
                'expendituresCount': len(apiData['objects'][0]['expenditures']),
                'firstExpenditure': firstExpenditure,
                'spendingDays': spendingDays,
                'spentPerDay': "{0:.2f}".format(spentPerDay),
                'spentPerSecond': "{0:.2f}".format(calculateSpentPerSecond(spentPerDay)),
                'timestamp': dt.datetime.strftime(dt.datetime.now(), dateFormat)
            }

            # store API call results in redis for one hour
            redis.setex(candidate_nick, json.dumps(responseJSON), redisDuration)
    return responseJSON



@app.route('/candidate/<string:candidate_nick>', methods=['GET'])
@cross_origin()
def get_candidate(candidate_nick):
    # return JSON data about requested candidate *or* error message
    return jsonify(get_cand_expenditures(candidate_nick))


# might as well have something on the home page, eh?
@app.route('/')
def index():
    hits = redis.get('indexhits')

    if (hits and int(hits) > 2):
        strang = 'You know why you visited this time, but what do you think the other {} visits were about?'.format(int(hits) - 1)
    else:
        strang = 'My, how nice of you to visit.'

    redis.incr('indexhits')

    return strang


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
