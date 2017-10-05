# -*- coding: utf-8 -*-

from flask import Flask, jsonify
from flask_cors import CORS, cross_origin
import firebase_admin
from firebase_admin import credentials, db

import collections
import datetime as dt
import json
import random
import requests
import os
import time
import logging

import boto3
dynamodb = boto3.resource('dynamodb')

env_suffix=''
env = os.environ.get('ENV')
if env:
    env_suffix = '-'+env
else:
    env_suffix = '-dev'

cache = dynamodb.Table('expenditures_cache'+env_suffix)
fact_oftheday_table = dynamodb.Table('fact_of_the_day'+env_suffix)


from candidates import candidates

app = Flask(__name__)

cred = firebase_admin.credentials.Certificate(json.loads(os.environ['cert']));
firebase_app = firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://illinois-calc.firebaseio.com/'
})


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


# this just works because hours, minutes and seconds are easy to pluralize
def plural(word, count):
    res = word
    if count > 1:
        res += 's'
    return res


# convenience during development/testing
@app.route('/clear', methods=['GET'])
def clear():
    for c in candidates:
        cache.delete_item(
            Key={
                'id': c.get('id')
            }
        )

    return 'cache cleared'


def retrieve_random_fact(of_the_day):
    rand_fact = None

    used_facts = []
    if of_the_day:
        # ID 1 is the cached result and 2 is the list of used facts
        # Check the cache first
        fact_response = fact_oftheday_table.get_item(
            Key={
                'id': '1'
            }
        )
        if 'Item' in fact_response:
            item = fact_response['Item'];
            cached_json = item['json']
            rand_fact = json.loads(cached_json)

        used_fact_response = fact_oftheday_table.get_item(
            Key={
                'id': '2'
            }
        )
        if 'Item' in used_fact_response:
            item = used_fact_response['Item'];
            cached_json = item['json']
            used_facts = json.loads(cached_json)

    if not rand_fact:
        facts_ref = db.reference('facts')
        #weirdly, leaving out the start_at makes it return a list
        #instead of a dict so we can't see the keys
        allfacts = facts_ref.order_by_key().start_at('0').get()
        all_fact_ids = []
        for key, value in allfacts.items():
            all_fact_ids.append(key)

        # if we don't have any facts left, then reset the used list
        if len(used_facts) >= len(all_fact_ids):
            used_facts = []
        for used_fact_key in used_facts:
            all_fact_ids.remove(used_fact_key)

        rand_fact_id = random.choice(all_fact_ids)
        used_facts.append(rand_fact_id)

        rand_fact = allfacts[rand_fact_id]

        if of_the_day:
            # Store in dynamo since we missed the cache
            expireTime = int((dt.datetime.today() + dt.timedelta(days=1)).timestamp())
            fact_oftheday_table.put_item(
                Item={
                    'id': '1',
                    'ttl': expireTime,
                    'json': json.dumps(rand_fact)
                }
            )
            fact_oftheday_table.put_item(
                Item={
                    'id': '2',
                    'json': json.dumps(used_facts)
                }
            )

    return rand_fact

def generate_response (rand_fact):
    cand_expenditures = get_cand_expenditures('rauner')

    # get it before rounding
    spentPerDay = calculateSpentPerDay(float(cand_expenditures['spendingDays']),
                                       float(cand_expenditures['total']))
    spentPerSecond = calculateSpentPerSecond(spentPerDay)
    secondsPerFactUnit = float(rand_fact['amount']) / spentPerSecond

    mins, secs = divmod(secondsPerFactUnit, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)

    text = "#RaunerSpends the %s in " % rand_fact['item']
    prevNum = False
    timecomponents = []
    if days:
        timecomponents.append("%d days" % days)
    if hours:
        timecomponents.append("%dhrs" % hours)
    if mins:
        timecomponents.append("%dmins" % mins)
    if secs:
        timecomponents.append("%ds" % secs)
    text += ", ".join(timecomponents)

    text += " [%s]" % rand_fact['source']

    resp = {'text': text}

    return resp


@app.route('/facts/random/oftheday', methods=['GET'])
@cross_origin()
def get_random_fact_oftheday():
    rand_fact = retrieve_random_fact(True)
    resp = generate_response(rand_fact)
    return jsonify(resp)


@app.route('/facts/random', methods=['GET'])
@cross_origin()
def get_random_fact():
    # pick a random fact from the db
    # pick a random candidate and get their numbers
    # calculate stuff and return the text


    rand_fact = retrieve_random_fact(False)
    resp = generate_response(rand_fact)
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
        # cachedJSON = redis.get(candidate_nick)
        response = cache.get_item(
            Key={
                'id': candidate_nick
            }
        )

        # if data found in redis, use it
        if 'Item' in response:
            item = response['Item'];
            cachedJSON = item['json']
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
            # redis.setex(candidate_nick, json.dumps(responseJSON), redisDuration)

            expireTime = int(time.time())+(redisDuration*1000);
            cache.put_item(
                Item={
                    'id': candidate_nick,
                    'ttl': expireTime,
                    'json': json.dumps(responseJSON)
                }
            )
    return responseJSON


@app.route('/candidate/<string:candidate_nick>', methods=['GET'])
@cross_origin()
def get_candidate(candidate_nick):
    # return JSON data about requested candidate *or* error message
    return jsonify(get_cand_expenditures(candidate_nick))


# might as well have something on the home page, eh?
#@app.route('/')
#def index():
#    hits = redis.get('indexhits')
#
#    if (hits and int(hits) > 2):
#        strang = 'You know why you visited this time, but what do you think the other {} visits were about?'.format(int(hits) - 1)
#    else:
#        strang = 'My, how nice of you to visit.'
#
#    redis.incr('indexhits')
#
#    return strang


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
