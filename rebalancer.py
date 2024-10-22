##########################################
#                                        #
#  eTrade tax loss harvesting script     #
#                                        #
#  Daniel Guetta, danguetta.github.io    #
#                                        #
########################################## 

# =======================
# =   Import packages   =
# =======================

# Logging
import logging
from logging.handlers import RotatingFileHandler

# Configuration parser
import configparser

# Packages for authentication
from rauth import OAuth1Service
import webbrowser

# Progress bars for loops
from tqdm import tqdm

# Pandas, numpy, random numbers
import pandas as pd
import numpy as np
import random

# Date and time
import datetime
import pytz
import holidays
import time

# json processing
import json

# Files
import os

# Jupyter display
from IPython.display import display, Markdown, HTML
import matplotlib.pyplot as plt
import seaborn as sns

# Optimizer
import scipy.optimize as opt

import jinja2
assert jinja2.__version__ >= '3.0.0', 'Please upgrade Pandas to a version >= 3.0.0. You can do this by running pip install jinja2 --upgrade'

assert pd.__version__ >= '1.5.1', 'Please upgrade Pandas to a version >= 1.5.1. You can do this by running pip install pandas --upgrade'

# =================
# =   Constants   =
# =================

TOLERANCE = 0.02
CASH_NAME = 'Cash'
OTHER_NAME = 'Other'

# =====================
# =   Error Classes   =
# =====================

class ApiQueryError(Exception):
    '''Exception raised when the eTrade API returns an error
    
    Attributes
    ----------
      - url          : the URL for the API request that was sent
      - data         : the data that was sent to the request
      - status_code  : the status code returned from the request
      - response     : the full response from the API
    '''
    
    def __init__(self, url, data, status_code, response):
        '''Initializes API query errors
        
        Arguments
        ---------
          See class attributes
        '''
        
        self.url         = url
        self.data        = data
        self.status_code = status_code
        self.response    = response
        
        super().__init__('Something wrong happened with the eTrade request; the '
                         'log file will have the full request and its response; '
                         'alternatively, see the documentation for this error to '
                         'access this data.')

class ValidationError(Exception):
    '''Exception raised when input data is incorrect
    
    Attributes
    ----------
      - input_vaue : the value that was input
      - message    : the message to display
    '''
    
    def __init__(self, message, input_value=None):
        '''Initializes validation errors
        
        Arguments
        ---------
          See class attributes
        '''
        
        self.input_value = input_value
        self.message     = message + (f'(input value: {input_value})' if input_value is not None else '')
        
        super().__init__(self.message)

class ExpectedCostError(Exception):
    '''Exception raised when the expected cost of an order is far from the estimated cost
    
    Attributes
    ----------
      - expected_cost
      - estimated_cost
    '''
    
    def __init__(self, expected_cost, estimated_cost):
        '''Initialize expected cost errors
        
        Arguments
        ---------
          See class attributes
        '''
        
        self.expected_cost = expected_cost
        self.estimated_cost = estimated_cost
        
        super().__init__('The expected cost for this order was too far from the estimated '
                            f'cost (expected cost: ${expected_cost:,.2f}, estimated cost: '
                            f'${estimated_cost:,.2f})')

class OrderPreviewError(Exception):
    '''Exception raised when we attempt to place an order with an invalid preview
    
    Attributes
    ----------
      - message
    '''
    
    def __init__(self, message):
        '''Initialize order preview errors
        
        Argument
        --------
          See class attributes
        '''
        
        self.message = message
        
        super().__init__(message)

class RebalancerError(Exception):
    '''Exception raised when the rebalancer errors out
    
    Attributes
    ----------
      - message
    '''
    
    def __init__(self, message):
        '''Initialize order preview errors
        
        Argument
        --------
          See class attributes
        '''
        
        self.message = message
        
        super().__init__(message)

def market_open():
    '''Determines whether the markets are currently open
    
    This isn't foolproof (for example, it doesn't include the early market close
    on the day after thanksgiving)    
    '''
    
    cur_date = datetime.datetime.now(pytz.timezone('America/New_York'))
    
    if (cur_date.hour <= 8) or ((cur_date.hour == 9) and (cur_date.minute < 30)) or (cur_date.hour >= 16):
        return False
    
    if (cur_date.weekday() >= 5):
        return False
    
    if (cur_date in holidays.NYSE()):
        return False
    
    return True
    
# ===============
# =   Classes   =
# ===============

class TargetAssetclass:
    '''Specifies a target asset class in a portfolio (eg: domestic stocks)
    
    Attributes
    ----------
      - target (float) : a number between 0 and 100, specifying the percentage
                         of the portfolio that should comprise this asset class
      - name (string)  : a plain English description of this asset class (eg:
                         "domestic stocks")
      
    Methods
    -------
      - securities : a method that, given a badness level, returns a list of stock
                     tickers with that badness level, in the order they were added
                     to the class
    '''
    
    def __init__(self, target, name, securities, badness_scores):
        '''Initialize a target asset class
        
        Arguments
        ---------
          - target (float)    : a number between 0 and 100, specifying the percentage
                                of the portfolio that should comprise this asset class.
                                This number should be an integer, to avoid Python float
                                errors
          - name (string)     : a plain English description of the asset class
          - securities (list) : a list of strings, each of which is a ticker for one
                                of several correlated securities that can be used to
                                meet this asset class
          - badness_scores (list) : a list of integers >= 1, at least one of which
                                should be 1, with the same length as securities. Each
                                entry specifies how "undesirable" the security in
                                question is. The algorithm will always try to sell
                                securities with higher badness scores and buy
                                securities with lower badness scores (see the
                                description of the algorithm for details). These
                                badness scores should be consecutive integers.
        '''
        
        assert 0 < target <= 100, ('The target for this asset class should be greater '
                                                    'than 0 and less than or equal to 100' )
        
        assert int(target) == target, 'The target for this asset class should be an integer'
        
        assert len(badness_scores) == len(securities), ( 'Every security should have an '
                                                'associated badness score' )
        
        assert all([i is None or i == int(i) for i in badness_scores]), ( 'Badness scores must all be '
                                                'integers' )
        
        assert all([i is None or i >= 1 for i in badness_scores]), ( 'Badness scores should all be >= 1' )
        
        assert any([i == 1 for i in badness_scores]), ( 'At least one badness score must be == 1' )
        
        # Check the badness scores are consecutive
        unique_scores = sorted(list(set(badness_scores) - set([None])))
        assert all([unique_scores[i+1] - unique_scores[i] == 1
                                    for i in range(len(unique_scores)-1)]), 'Badness scores should be consecutive'
        
        if name in [OTHER_NAME, CASH_NAME]:
            raise ValidationError(f'"{OTHER_NAME}" and "{CASH_NAME}" are reserved names; please '
                                                                'call your asset class something else.')
        
        self.target          = target
        self.name            = name
        self._securities     = [i.upper() for i in securities]
        self._badness_scores = badness_scores
    
    def securities(self, badness_score=None):
        '''Returns the securities in this asset class with a specific badness score
        
        Arguments
        ---------
          - badness_score (int) : the badness score to extract; if None, all securities
                                  in this asset class are returned
        
        Returns
        -------
           A list of strings, each containing a ticker for a security in that
           asset class with the badness score specified.
           
           If no securities in this asset class have the specified badness scores,
           returns an empty list.
        '''
        
        return [i for i, j in zip(self._securities, self._badness_scores)
                                    if (badness_score is None) or (j == badness_score)]
    
    @property
    def badness_scores(self):
        '''Returns a list of badness scores for all securities in this asset class
        
        A copy is returned to make sure we don't inadvertently modify it
        '''
        
        return list(self._badness_scores)
    
class TargetPortfolio:
    '''Specifies a target portfolio
    
    Properties
    ----------
      - target_assetclasses : a dictionary in which each key is the name of a target asset class,
                              and the value is a TargetAssetclass object
    
    Methods
    -------
      - add_assetclass : method to add an asset class to the portfolio
    '''
    
    def __init__(self):
        '''Initialize a TargetPortfolio
        
        No arguments
        '''
        
        self._validated = False
        self._target_assetclasses = {}
    
    def add_assetclass(self, *args, **kwargs):
        '''Add an asset class to the portfolio
        
        If the target portfolio has been validated, do not allow extra asset classes
        to be added to it
        
        Arguments
        ---------
          See the __init__ method of the TargetAssetclass class
        '''
        
        assert not self._validated, 'Target portfolio has been validated; no further asset classes can be added'
        
        target_assetclass = TargetAssetclass(*args, **kwargs)
        
        # Ensure the name doesn't exist yet
        assert target_assetclass.name not in self._target_assetclasses, 'Target asset class name already exists'
        
        self._target_assetclasses[target_assetclass.name] = target_assetclass
        
    def validate(self):
        '''Validate a target portfolio
        
        Will raise an error if the asset classes in the target portfolio do not
        sum to 1, or if some securities appear in more than one asset class
        '''
        
        # Ensure target asset classes sum to 100
        assert sum([a.target for a in self._target_assetclasses.values()]) == 100, 'Targets must sum to 100 across asset classes'
        
        # Ensure no security exists in more than one asset class
        all_securities = set()
        for a in self._target_assetclasses.values():
            if len(all_securities.intersection(a.securities())) > 0:
                raise 'Some securities exist in more than one asset class; please fix'
            
            all_securities.update(a.securities())
            
        self._validated = True
    
    @property
    def target_assetclasses(self):
        '''Will return the list of target asset classes. WARNING: a mutable object is returned; do not modify
        
        Can only be accessed once self.validate has been run
        '''
        
        assert self._validated, 'Please validate the target portfolio before using it.'
        
        return self._target_assetclasses
    
    def find_security(self, symbol):
        '''Identify the asset class a security is part of, and its badness in that asset class
        
        Arguments
        ---------
          - symbol (string) : the stock ticker for that security
        
        Returns
        -------
          A dictionary with two entries
            - assetclass  : the asset class the security is a part of
            - badness     : its badness
          If the security is not found in the target portfolio,
            {'assetclass' : OTHER_NAME, 'badness' : None}
          will be returned
        '''
        
        for a_name, a in self.target_assetclasses.items():
            if symbol in a.securities():
                return {'assetclass' : a_name, 'badness' : a.badness_scores[a.securities().index(symbol)]}
        
        return {'assetclass' : OTHER_NAME, 'badness' : None}
        
class EtradeConnection:
    '''Connection to the eTrade API
    
    This class contains methods for all the requests we will make of the
    eTrade API.
    
    Most of the code in this class was adapted from the sample Python code
    provided here: https://developer.etrade.com/home
    
    Methods
    -------
      - list_accounts      : list all eTrade accounts
      - get_cash_balance   : get the cash balance for a specific account
      - get_positions      : get all positions in a specific account
      - get_lots           : get all the tax lots that make up on specific
                             position
      - get_recent_trades  : get a list of all securities bought and sold
                             in the last 30 days
      - get_current_price  : get the current price of a security
      - execute_order      : execute a buy or sell order
    '''

    # Save the base URL that should be prepended to all API requests
    _base_url = 'https://api.etrade.com'
    
    def __init__(self, config_file, log_file = None):
        '''Create a connection to the eTrade API and authenticate
        
        This function will launch a browser to authenticate with eTrade; the
        relevant webpage will ask you to verify access and give you a code
        which Python will prompt you for.
        
        Arguments
        ---------
          - config_file (string) : the filename, or path, to a configuration
                                   file, which includes CONSUMER_KEY and
                                   CONSUMER_SECRET for the eTrade API. See
                                   the docs to this code for details
          - log_file (string)    : every request to the eTrade API will be
                                   logged; this argument determines how the
                                   logging is done
                                     * If None, logging will be done to a file
                                       named using today's date and time in a
                                       "logs" folder
                                     * If "screen", messages will be printed
                                       to the screen
                                     * If any other string, messages will be
                                       logged to a file with that name
        '''
            
        # Set up a logger if required
        # ---------------------------
        if log_file is None:
            if not os.path.exists('logs'):
                os.mkdir('logs')
                
            log_file = 'logs/' + datetime.datetime.now().strftime('%y-%m-%d %H%M') + '.log'
                
        self._log_file = log_file
        
        if log_file != 'screen':
            self._logger = logging.getLogger('my_logger')
            self._logger.setLevel(logging.DEBUG)
            handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
            FORMAT = "%(asctime)-15s %(message)s"
            fmt = logging.Formatter(FORMAT, datefmt='%m/%d/%Y %I:%M:%S %p')
            handler.setFormatter(fmt)
            self._logger.addHandler(handler)   
    
        # Save the config file name
        # -------------------------
        self._config_file = config_file
    
        # Connect to the eTrade API
        # -------------------------
        etrade = OAuth1Service(name              = "etrade",
                               consumer_key      = self._config['CONSUMER_KEY'],
                               consumer_secret   = self._config['CONSUMER_SECRET'],
                               request_token_url = "https://api.etrade.com/oauth/request_token",
                               access_token_url  = "https://api.etrade.com/oauth/access_token",
                               authorize_url     = "https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
                               base_url          = "https://api.etrade.com")
        
        # Step 1 - Request a token
        request_token, request_token_secret = (
                etrade.get_request_token(params={"oauth_callback": "oob", "format": "json"}))
                
        # Step 2 - get the code from the etrade website
        authorize_url = etrade.authorize_url.format(etrade.consumer_key, request_token)
        webbrowser.open(authorize_url)
        text_code = input("Please accept agreement and enter text code from browser: ")
        
        # Step 3 - Exchange the authorized request token for an authenticated OAuth 1 session
        self._session = etrade.get_auth_session(request_token,
                                               request_token_secret,
                                               params={"oauth_verifier": text_code})
    
    @property
    def _config(self):
        '''Extract the consumer key and secret from a configuration file
        
        These values are not saved in the class to ensure they cannot be
        printed inadvertently
        
        Returns
        -------
          A dictionary with two entries - CONSUMER_KEY and CONSUMER_SECRET
        '''
        
        config = configparser.ConfigParser()
        config.read(self._config_file)
        
        return {'CONSUMER_KEY'    : config["DEFAULT"]["CONSUMER_KEY"],
                'CONSUMER_SECRET' : config["DEFAULT"]["CONSUMER_SECRET"]}
    
    def _log_message(self, m):
        '''Log a message
        
        Depending on how the class was initiated, this will be to the screen
        or to the file; see __init__ function
        
        Arguments
        ---------
          - m (string) : the message to be logged
        '''
        
        if self._log_file == 'screen':
            print(m)
        else:
            self._logger.debug(m)
        
    def _query(self, url, method, **kwargs):
        '''Run a query against the eTrade API
        
        This function will look the request, and its response, to the log file
        
        Arguments
        ---------
          - url (string) : the URL for the query WITHOUT the prefix; for
                           example: "/v1/accounts/list.json"
          - method (string) : GET or POST
          - Any other arguments will be passed directly to the request
        
        Returns
        -------
          A dictionary with the response from eTrade
        '''
        
        # Add the prefix to the URL
        url = self._base_url + url
        
        # Find the relevant method
        assert method in ['GET', 'POST'], 'Only GET and POST methods are supported'
        
        if method == 'GET':
            method = self._session.get
        elif method == 'POST':
            method = self._session.post
        
        # Log the request
        self._log_message(f'Request URL: {url}')
        self._log_message(f'Request method: {method}')
        self._log_message(f'kwargs: {kwargs}')
        
        # Run the request
        response = method(url, header_auth=True, **kwargs)
        
        # Log the response
        self._log_message(f'Request response: {response.text}')
        self._log_message('-----------------------------------------')
        
        # Make sure the response was successful
        if response.status_code not in [200, 204]:
            raise ApiQueryError(url, kwargs, response.status_code, response.text)
            
        # Return the response in json form; make the error more legible if the json
        # can't be read
        try:
            if response.status_code == 204:
                return None
            else:
                return response.json()
        except:
            raise 'Something wrong happened with the eTrade request; see the log file'
    
    @staticmethod
    def _dict_to_xml(o):
        '''
        This function takes a Python object, and converts it to xml format. It accepts a
        single argument, the object to be converted, and returns the xml version
        '''
        
        out = []
        
        if type(o) == list:
            out = [EtradeConnection._dict_to_xml(i) for i in o]
        elif type(o) == dict:
            for i in o:
                this_item = EtradeConnection._dict_to_xml(o[i])
                
                if '\n' not in this_item:
                    out.append(f'<{i}>{this_item}</{i}>')
                else:
                    out.append(f'<{i}>\n' + '\n'.join(['    ' + ii for ii in this_item.split('\n')]) + f'\n</{i}>')
        else:
            out.append(str(o))
        
        return '\n'.join(out)

    def list_accounts(self):
        '''Returns all accounts for this eTrade user
        
        eTrade API docs: https://apisb.etrade.com/docs/api/account/api-account-v1.html
        
        Returns
        -------
          A list of dictionaries, each containing one eTrade account. Each dictionary
          will include the following elements
            - account_number : the account number, as visible on the front-end
            - account_id_key : the account ID key, which will be used to identify the
                               account on the back-end of the API
        '''
        
        # Run the request
        response = self._query(url='/v1/accounts/list.json', method='GET')
        
        # Get the accounts
        response = response['AccountListResponse']['Accounts']['Account']
        
        # Return
        return [{'account_number' : str(i['accountId']),
                 'account_id_key' : str(i['accountIdKey'])}
                                            for i in response]
    
    def get_cash_balance(self, account_id_key):
        '''Returns the cash balance in a specified account
        
        eTrade API docs: https://apisb.etrade.com/docs/api/account/api-balance-v1.html
        
        Arguments
        ---------
          - account_id_key (string) : an account ID key, obtained from
                                      list_accounts
          
        Returns
        -------
          The cash balance in the said account
        '''
        
        # Run the request
        response = self._query(url    = '/v1/accounts/' + account_id_key + "/balance.json",
                               method = 'GET',
                               params = {'instType':'BROKERAGE', 'realTimeNAV':True})
        
        # Return
        return response['BalanceResponse']['Computed']['cashAvailableForInvestment']

    def get_positions(self, account_id_key):
        '''Returns all positions in a specified account
        
        eTrade API docs: https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html
        
        This function does *not* return lot details, or any information about
        purchase date, purchase prices, etc... This is because in portfolios with
        *many* positions, it will take a long time to download all lot data. If we
        were to do all that in this function, and something goes wrong during the
        process, all the data would be lost. Instead, we provide a get_lots function,
        which can be called for each position in 
        
        Note that by default, this function errors out if it finds any securities
        that are not equities, and any positions that are not LONG positions. This
        is because these are the only securities I had in my portfolio, and
        therefore the only securities I could test the script on.
        
        Arguments
        ---------
          - account_id_key (string) : an account ID key, obtained from
                                      list_accounts
        
        Returns
        -------
          A list of dictionaries, each containing one position. Each dictionary
          will include the following elements
            - position_id      : the eTrade position ID to identify the position
            - symbol           : the stock ticker
            - market_value     : the current market value of the position
            - quantity         : the number of shares owned
            - sub_type         : the securitySubType from the eTrade API
            - lot_details_url  : an eTrade API link to obtain details of all lots for
                                 this position
          
          Note that the dictionary does not contain any information about the
          purchase price, purchase date, etc..., because there may be multiple
          tax lots in each positions. These lot details have to be obtained
          separately using get_lots.
        '''
        
        positions = []
        
        # Download all transactions; eTrade uses a "paging" system; if the response
        # contains a nextPageNo element, there's another page
        response = {'nextPageNo':1}
        while response.get('nextPageNo', None):
            response = self._query(url    = '/v1/accounts/' + account_id_key + '/portfolio.json',
                                   method = 'GET',
                                   params = {'pageNumber' : response['nextPageNo']})
            
            response = response['PortfolioResponse']['AccountPortfolio'][0]
            
            for position in response['Position']:
                assert position['positionType']            == 'LONG'
                assert position['Product']['securityType'] == 'EQ'
                
                # Ensure the lotsDetails ulr begins with self._base_url, and remove
                # it from the start of the request, because it will be added back
                # later when we run it through .query
                assert position['lotsDetails'].startswith(self._base_url), ( 'lotsDetails API '
                                      'url does not begin with the base URL given in the class.' )
                lots_details = position['lotsDetails'][len(self._base_url):]
                
                positions.append({'position_id'     : str(position['positionId']),
                                  'symbol'          : position['symbolDescription'],
                                  'market_value'    : position['marketValue'],
                                  'quantity'        : position['quantity'],
                                  'sub_type'        : position['Product'].get('securitySubType', None),
                                  'lot_details_url' : lots_details})
        
        return positions
            
    def get_lots(self, lot_details_url):
        '''Returns all lots making up a specific position
        
        eTrade API docs: https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html#/definitions/PositionLot
        
        Arguments
        ---------
          - lot_details_url : a URL for the specific API request to get the lots,
                              from get_positions
        
        Returns
        -------
          A list of dictionaries, each containing one lot. Each dictionary
          will include the following elements
            - position_id     : the ID of the position this lot is part of
            - position_lot_id : the ID of the lot
            - symbol          : the stock ticker for the security in question
            - price           : the unit price at which securities in this tax lot were
                                bought
            - market_value    : the current market value of the tax lot
            - quantity        : the quantity of the security in the lot
            - acquired_date   : the date on which the securities in this lot were acquired;
                                I've found this is sometimes missing when eTrade doesn't
                                have this data
        
        '''
        
        lots = []
        
        response = self._query(url    = lot_details_url,
                               method = 'GET')['PositionLotsResponse']['PositionLot']
        
        for l in response:
            lots.append({'position_id'      : str(l['positionId']),
                         'position_lot_id'  : str(l['positionLotId']),
                         'price'            : l['price'],
                         'market_value'     : l['marketValue'],
                         'quantity'         : l['remainingQty'],
                         'acquired_date'    : l.get('acquiredDate', None)})
        
        return lots
    
    def get_recent_trades(self, account_id_key, window = 31):
        '''Retrieves all securities bought or sold in the last window days
        
        eTrade API docs: https://apisb.etrade.com/docs/api/order/api-order-v1.html#/definition/getOrders
        
        Arguments
        ---------
          - account_id_key (string) : an account ID key, obtained from
                                      list_accounts
          - window (integer)        : the number of days over which to download
                                      orders. By default, this will be 31, to retrieve
                                      all orders that are relevant in determining
                                      whether we have a wash sale
        
        Returns
        -------
          A dictionary with two entries
            - bought : a list of all security tickers that were bought in the
                       window
            - sold   : a list of all security tickers that were sold in the window
        '''
        
        out = {'bought':[], 'sold':[]}
        
        # Create a parameters dictionary
        params =  {'count'    : 100,
                  'fromDate' : (datetime.datetime.today() - datetime.timedelta(days=window)).strftime('%m%d%Y'),
                  'toDate'   : (datetime.datetime.today() + datetime.timedelta(days=1)).strftime('%m%d%Y')}

        # Download all orders; eTrade uses a "paging" system; if the response
        # contains a marker element, there's another page
        response = {'marker':0}
        while 'marker' in response:
            params['marker'] = response['marker']
            response = self._query(f'/v1/accounts/{account_id_key}/orders.json',
                                   method = 'GET',
                                   params = params)
            
            if response is not None:
                response = response['OrdersResponse']
                for t in response['Order']:
                    if t['OrderDetail'][0]['status'] != 'CANCELLED':
                        order_action = t['OrderDetail'][0]['Instrument'][0]['orderAction']
                        symbol = t['OrderDetail'][0]['Instrument'][0]['Product']['symbol']
                        
                        if order_action == 'BUY':
                            out['bought'].append(symbol)
                        elif order_action == 'SELL':
                            out['sold'].append(symbol)
                        else:
                            raise 'Unknown order action found'
            else:
                response = {}
                
        # Remove duplicates
        out = {i:list(set(out[i])) for i in out}
        
        return out
    
    def get_current_price(self, symbol):
        '''Gets the current price (average of bid and ask) for a security
        
        eTrade API docs: https://apisb.etrade.com/docs/api/market/api-quote-v1.html
        
        Arguments
        ---------
          - symbol (string) : the ticker for the security
       
        Returns
        -------
          A single float, with the price
        '''
        
        response = ( self._query(url    = f'/v1/market/quote/{symbol}.json',
                                 method = 'GET')
                                 ['QuoteResponse']['QuoteData'][0]['All'] )
        
        return (( response['bid'] + response['ask'] )/2)

    def execute_order(self, account_id_key, symbol, price_type, order_action, quantity,
                            expected_cost = None, limit_price = None, lots = None,
                                preview_only=False, preview_result=None):
        '''Executes an order, WARNING: this by-passes eTrade's requirement to preview orders unless preview_only=True
        
        eTrade API docs: https://apisb.etrade.com/docs/api/order/api-order-v1.html#/definition/orderPreview
                         https://apisb.etrade.com/docs/api/order/api-order-v1.html#/definition/orderPlace
        
        This function will take an order, preview it using the eTrade API, and
        then immediately execute it.
        
        If preview_only=False, the order will be placed immediately. If preview_only=True,
        the order will be previewed in the eTrade API, and the estimated proceeds will be
        returned as well as a preview ID that can then be used to execute the order
        
        *Important note* : In an ideal world, it'd be possible to place multiple orders
        using ONE API call, to reduce latency between orders and reduce the chance
        market conditions could change between calls. I spent a while trying to figure
        out if that was possible, but failed; it's unclear to me whether this is because
        the API doesn't allow it, or because I can't figure out the syntax. Here's
        evidence in each direction
          - Evidence it might be possible: the API states that the "instrument" argument
            is an array, and the BUY vs. SELL instruction is inside that instrument
            object; in other words it might be possible to include two instruments with
            two different shares, one buy, one sell
          - Evidence it might not be possible: the limit_price argument is *outside* that
            instrument array, so the limit_price would have to be shared across all
            instruments
        
        Arguments
        ---------
          - symbol (string)       : the ticker of the security to trade
          - price_type (string)   : MARKET or LIMIT
          - order_action (string) : BUY or SELL
          - quantity (integer)    : the number of securities to buy
          - expected_cost (float) : the expected cost of this order; positive if this is a
                                    buy, negative if this is a sell. If the estimated proceeds
                                    deviate by more than a fraction TOLERANCE from this number,
                                    an exception is raised. If this is omitted, no checks will
                                    be done
          - limit_price (float)   : limit price, if the order is LIMIT
          - lots (list)           : for sell orders only, a dictionary indicating the tax lots
                                    to sell from. Each of these dictionaries will contain two
                                    entries; position_lot_id and quantity. The sum of quantities
                                    in the dictionaries should be equal to the overall quantity.
                                    If omitted, eTrades default selling priority will be used to
                                    decide what lots to sell from.
          - preview_only (bool)   : If True, the order is previewed, but not executed
          - preview_result (dict) : If this order was previously previewed and now needs to be
                                    executed, this argument should contain the preview result
                                    that was returned when the order was previewed
        
        Returns
        -------
          A dictionary with one or more elements:
            - estimated_cost : the estimated cost of this order (negative for a sale)
                               returned by eTrade
            - messages       : Only returned if preview_only=False. Any messages in the
                               eTrade response
            - outcome        : Only returned if preview_only=False. Equal to 'PLACED' if
                               the order was successfully placed, 'QUEUED' if the order was
                               queued (for example, if the order was placed outside market
                               hours), and None if we can't determine what happened
            - preview_result : Only returned if preview_only=True; this will contain
                               the preview result which can then be used to execute the
                               trade
                               
        '''
        
        # Validate inputs
        # ---------------
        if order_action not in ['BUY', 'SELL']:
            raise ValidationError('order_action must be BUY or SELL', order_action)
        if price_type not in ['MARKET', 'LIMIT']:
            raise ValidationError('price_type must be MARKET or LIMIT', price_type)
        if (price_type == 'LIMIT') and (limit_price is not None):
            raise ValidationError('limit_price must be provided for a limit order', limit_price)
        if ( (order_action == 'SELL')
                    and (lots is not None)
                        and quantity != sum([i['quantity'] for i in lots]) ) :
            raise ValidationError('Quantity provided in lots does not match total quantity', lots)
        
        # Step 1; create the order payload
        # --------------------------------
        
        # Create a random order ID, unless it was already generated during
        # a preview
        if preview_result is not None:
            client_order_id = preview_result[1]
        else:
            client_order_id = str(random.randint(1000000000, 9999999999))[:8]
        
        order = {'allOrNone'     : 'true',
                 'priceType'     : price_type,
                 'orderTerm'     : 'GOOD_FOR_DAY',
                 'marketSession' : 'REGULAR',
                 'stopPrice'     : None,
                 'limitPrice'    : limit_price,
                 'Instrument'    : {'Product'      : {'securityType' : 'EQ', 'symbol' : symbol},
                                     'orderAction'  : order_action,
                                     'quantityType' : 'QUANTITY',
                                     'quantity'     : quantity} }
        
        if (order_action == 'SELL') and (lots is not None):
            order['Instrument']['Lots'] = [{'Lot':{'id':i['position_lot_id'], 'size':i['quantity']}}
                                                                                        for i in lots]
               
        order_payload =  {'orderType'     : 'EQ',
                          'clientOrderId' : client_order_id,
                          'Order'         : order}       
        
        # Create query headers
        headers = {'Content-Type': 'application/xml', 'consumerKey': self._config['CONSUMER_KEY']}
        
        # Step 2; preview the order
        # -------------------------
        
        # Build the preview payload; note that in theory, we should be able to pass
        # the dictionary directly to the request, but for some reason it doesn't
        # seem to work and the eTrade API is like a surly teenager - it doesn't tell
        # you *what* went wrong, it just tells you there's an error. I tried as hard
        # as possible to debug this, but I was quickly losing the will to live and
        # gave up - instead, I'm first converting the dictionary to XML, and then
        # passing that to eTrade, which seems to work beter
    
        # Convert the json to XML
        xml_payload = self._dict_to_xml({'PreviewOrderRequest' : order_payload})
    
        if preview_result is None:
            # Run the query
            try:
                response = self._query(url     = '/v1/accounts/' + account_id_key + '/orders/preview.json',
                                       method  = 'POST',
                                       headers = headers,
                                       data    = xml_payload)
            except ApiQueryError as e:
                # Check whether the error arises from lack of funds
                try:
                    e_json = json.loads(e.response)
                    
                    code = e_json['Error']['code']
                    message = e_json['Error']['message']
                except:
                    raise e
                    
                if (code == 8400) and ('This order cannot be accepted due to insufficient funds in your account' in message):
                    raise OrderPreviewError('The account does not contain enough funds to preview this order')
                else:
                    raise e
            
            # Get the estimated cost and preview ID
            estimated_cost = response['PreviewOrderResponse']['Order'][0]['estimatedTotalAmount']
            preview_id = response["PreviewOrderResponse"]["PreviewIds"][0]['previewId']
            
            # If an expected_cost was provided, check it matches the estimated cost
            if expected_cost is not None:
                if np.abs(expected_cost - estimated_cost)/np.abs(expected_cost) > TOLERANCE:
                    raise ExpectedCostError(expected_cost, estimated_cost)
            
            # If we're looking for a preview only, return
            if preview_only:
                # Create a preview_result, a tuple in which the first entry is
                # the preview_id, the second is the order_id, and the third is the
                # xml_payload; the reason we need to do this is that - astonishingly 
                # - when a preview ID is used to place an order, eTrade doesn't
                # check that the order that was used to *generate* the preview ID is
                # the same as the order that is *now being placed*. As such, we
                # return both the preview ID and the order that was used to preview
                # it, and then we'll check it later
                preview_result = (preview_id, client_order_id, xml_payload)
            
                return {'estimated_cost' : estimated_cost,
                        'preview_result' : preview_result}
        
        else:
            # Check that the preview provided was created based on the same
            # order (see the comment above)
            if preview_result[2] != xml_payload:
                raise OrderPreviewError('The preview result was obtained using a different order')
            
            preview_id = preview_result[0]
        
        # Step 3; execute the order
        # -------------------------
        
        # Add the preview ID to the order to allow eTrade to execute it
        order_payload['PreviewIds'] = {'previewId' : preview_id}
        
        # Create the payload
        xml_payload = self._dict_to_xml({'PlaceOrderRequest' : order_payload})
        
        # Run it
        try:
            response = self._query(url     =  '/v1/accounts/' + account_id_key + '/orders/place.json',
                                   method  = 'POST',
                                   headers = headers,
                                   data    = xml_payload)
        except ApiQueryError as e:
            # Check whether the error arises from the preview having timed out
            try:
                e_json = json.loads(e.response)
                
                code = e_json['Error']['code']
                message = e_json['Error']['message']
            except:
                raise e
            
            if (code == 1033) and ('we have timed out your original' in message):
                raise OrderPreviewError('The preview has timed out; the order cannot be placed')
            else:
                raise e
        
        # Step 4; return
        # --------------
        messages = response['PlaceOrderResponse']['Order'][0]['messages']['Message']
        message_codes = [m['code'] for m in messages]
        message_descs = [m['description'] for m in messages]
        
        # Attempt to figure out what happened
        if 1026 in message_codes:
            outcome = 'PLACED'
        elif 1027 in message_codes:
            outcome = 'QUEUED'
        else:
            outcome = None
        
        # Return
        return {'estimated_cost' : response['PlaceOrderResponse']['Order'][0]['estimatedTotalAmount'],
                'messages'       : ' & '.join(message_descs),
                'outcome'        : outcome}
        
class Account:
    '''eTrade account
    
    This class contains all the positions and lot details for a specific
    eTrade account.
    
    It should be initialized with a EtradeConnection object, which will be
    used to download all the account information from the api.
    
    Properties
    ----------
      - df_positions   : a DataFrame of all positions in the account
      - df_lots        : detailed tax lots information for all positions in the
                         acccount
      - cash           : amount of cash in the account
      - recent_trades  : securities bought and sold in the last 30 days
    '''
    
    def __init__(self, account_number = None, conn = None, validation_folder=None):
        '''Initialize an eTrade account and download all the securities therein
        
        I've found the API to - very rarely - be buggy, so to ensure portfolio
        information is accurate, it can be compared to a downloaded version
        from the eTrade front end (obtained as described below) as an additional
        integrity check, to make sure the data we're working with is accurate.
        
        Arguments
        ---------
          - account_number (string)    : the account number to be retrieved
          - conn (EtradeConnection)    : an EtradeConnection object that can be used to run
                                         queries against the Etrade API
          - validation_folder (string) : the path of a folder containing CSVs downloaded from the eTrade
                                         front-end by clicking on "View full portfolio" on the overview
                                         page, and clicking on the down arrow at the top-right-hand corner
                                         of the resulting page. The portfolio obtained through the API will
                                         be compared to the last file in that folder alphabetically by file
                                         name (so for example, naming files using the convention
                                         YYYY-MM-DD HH-MM.csv will always lead the last file to be the latest
                                         file) and if any of the assets or quantities are different, an error
                                         will be thrown. Also, if any of the market values diverge by more
                                         than 2*TOLERANCE, an error will be thrown.
                                         
                                         This is added in as an extra check. No check will be done if this
                                         argument is None
        '''
                
        self._account_number = account_number
        
        if account_number is None:
            print('Using the sample portfolio')
            return
        
        self._conn = conn
        self._validation_folder = validation_folder
        
        # Step 1; find the account
        # ------------------------
        print('Finding account')
        
        account = [i for i in self._conn.list_accounts() if i['account_number'] == self._account_number]
        assert len(account) == 1, 'Account number provided was not found'
        self._account_id_key = account[0]['account_id_key']
        
        # Step 2; download all positions and lots
        # ---------------------------------------
        print('Downloading positions and lots')
        
        self._download_data()
        
        # Step 3; parse positions and lots
        # --------------------------------
        print('Parsing lots and positions data')
        
        self._parse_data()
        
        # Step 4; validate with downloaded portfolios
        # -------------------------------------------
        
        if self._validation_folder is not None:
            print('Comparing to downloaded portfolio')
                
            self._validate_data()
        
        # Done
        print('Portfolio downloaded and parsed')
        
    def _download_data(self):
        '''Download position and lot data for this account
        
        This function takes no arguments and returns nothing, but it sets the
        self._raw_positions, self._raw_lots, and self._recent_trades variables
        '''
        
        self._raw_positions = self._conn.get_positions(self._account_id_key)
        
        self._raw_lots = []
        
        for p in tqdm(self._raw_positions):
            self._raw_lots.extend(self._conn.get_lots(p['lot_details_url']))

        self._recent_trades = self._conn.get_recent_trades(self._account_id_key)
        
        self._cash = self._conn.get_cash_balance(self._account_id_key)

    def _parse_data(self):
        '''Parse downloaded position data for this account
        
        This function takes no arguments and returns nothing, but it sets the
        self._df_lots and  self._df_positions variables
        '''
    
        # Step 1 - convert data to Pandas
        # -------------------------------
        self._df_positions = pd.DataFrame(self._raw_positions).drop(columns='lot_details_url')
        self._df_lots = pd.DataFrame(self._raw_lots).assign(gain = lambda x : x.market_value - (x.price*x.quantity))
        
        # Convert the unix timestamps to dates (note: the field returned by the API
        # only provides date, not time, so we strip time)
        self._df_lots.acquired_date = pd.to_datetime(self._df_lots.acquired_date, unit='ms').dt.date
                        
        # Step 2 - add a symbol column to the lots
        # ----------------------------------------
        assert self._df_positions.position_id.duplicated().sum() == 0, 'Some position IDs were downloaded twice'
        
        n_lots = len(self._df_lots)
        self._df_lots = pd.merge(self._df_lots,
                                 self._df_positions[['position_id', 'symbol']],
                                 how      = 'left',
                                 validate = 'many_to_one')
        assert len(self._df_lots) == n_lots, 'Error adding symbol to lots; rows created or dropped'
        
        # Step 3 - ensure the sum of the lots matches the positions
        # ---------------------------------------------------------
        aggregated_lots = self._df_lots.groupby('position_id')[['quantity', 'market_value']].sum().reset_index()
        
        compare = pd.merge(self._df_positions[['position_id', 'market_value', 'quantity']],
                           aggregated_lots.rename(columns={'quantity':'lot_quantity', 'market_value':'lot_market_value'}),
                           how='outer',
                           validate = 'one_to_one')
       
        # Ensure nothing was missing on either side
        assert compare.market_value.isnull().sum() + compare.lot_market_value.isnull().sum() == 0, 'Aggregated lot comparisson failed'
        
        # Ensure the quantities match
        assert (compare.quantity != compare.lot_quantity).sum() == 0, 'Some lot quantities do not match positions'
        assert (((compare.market_value - compare.lot_market_value).abs() / compare.market_value) > 0.01).sum() == 0, 'Some lot values do not match positions'
        
        # Step 4 - add gain and loss summaries to the positions
        # -----------------------------------------------------
        for col_name, query in (('lots_loss', 'gain < 0'), ('lots_gain', 'gain > 0')):
            self._df_positions = pd.merge(self._df_positions,
                                          self._df_lots
                                              .query(query)
                                              .groupby('position_id')
                                              .gain
                                              .sum()
                                              .reset_index()
                                              .rename(columns = {'gain':col_name}),
                                          how      = 'left',
                                          validate = 'one_to_one')
            
            self._df_positions[col_name] = self._df_positions[col_name].fillna(0)
        
        # Step 5 - re-order columns and set indexes
        # -----------------------------------------
        self._df_positions = self._df_positions[['position_id', 'symbol', 'market_value',
                                                            'quantity', 'sub_type', 'lots_loss',
                                                                'lots_gain']].set_index('symbol').copy()
        self._df_lots = self._df_lots[['position_id', 'position_lot_id', 'symbol', 'acquired_date',
                                                            'price', 'market_value', 'quantity', 'gain']].copy()
        
        # Warn about fractional lots
        # --------------------------
        if (self._df_lots.quantity != self._df_lots.quantity.astype(int)).sum() > 0:
            display(self._df_lots[self._df_lots.quantity != self._df_lots.quantity.astype(int)])
            print('WARNING: some of your lots contain a fractional number of shares. eTrade does not'  '\n'
                  '         allow the purchase of fractional shares, so these probably come from a'    '\n'
                  '         transfer from another broker. eTrade apparently only allows the sale of'   '\n'
                  '         fractional shares when the entire position is liquidated, so you may'      '\n'
                  '         encounter some trouble when the algorithm tries to trade.'               '\n\n'
                  '         See the comment in the code for potential ways to get rid of these lots.')
            
            # Potential way to get rid of fractional lots
            #   - If you are able to bunch together multiple lots in a way that the sum of quantities
            #     is an integer, you can sell that integer number of shares from the front-end and
            #     AFTER "previewing" the order, you can select the fractional lots as the ones to
            #     share
            #   - If the sum of all the fractional lots is fractional, you're out of luck. The only
            #     option I can think of (I haven't tried myself) is to transfer those fractional lots
            #     to a broker that does allow the sale
    
    def _validate_data(self):
        '''Compare API data to data downloaded from the front-end
        
        See __init__ function for a description of this comparisson
        
        Will raise an error if the API portfolio deviates from the front-end portfolio in
        ways described in __init__
        '''
        
        # Identify the latest file in the folder
        f_name = os.path.join(self._validation_folder, max(os.listdir(self._validation_folder)))
        
        # Step 1 - read the file
        # ----------------------
        # The format is super weird, but the code below reads it (hopefully) correctly
        
        with open(f_name, 'r') as f:
            # Read the file, skipping the first 10 lines, and splitting each row
            # on commas
            file = [i.split(',') for i in f.read().split('\n')[10:]]

        parsed_file = []
        headers     = file[0]

        for i in file[1:]:
            # While we have 10 columns, we have holdings. The last row has 11
            # columns and it contains cash holdings
            if len(i) == 10:
                parsed_file.append(i)
            else:
                # Ensure this row contains cash holdings
                assert i[0] == 'CASH'
                cash_holdings = float(i[-2])
                break
        
        # Put the raw data into a DataFrame
        df_web = ( pd.DataFrame(parsed_file, columns=headers)
                     .rename(columns = {'Symbol'       : 'symbol',
                                        'Quantity'     : 'quantity_web',
                                        'Value $'      : 'market_value_web'})
                     [['symbol', 'quantity_web', 'market_value_web']] )
        
        for col in ['quantity_web', 'market_value_web']:
            df_web[col] = df_web[col].astype(float)

        # Step 2 - compare to the downloaded data
        # ---------------------------------------
        
        # Ensure no position is missing from either side
        if set(self.df_positions.index) != set(df_web.symbol):
            print(f'Securities present in the API but not on the front end: {set(self.df_positions.symbol) - set(df_web.symbol)}')
            print(f'Securities present in the front end but not on the API: {set(df_web.symbol) - set(self.df_positions.symbol)}')
            raise
            
        # Compare the quantities
        comp = pd.merge(self.df_positions.reset_index(),
                        df_web,
                        how      = 'inner',
                        validate = 'one_to_one')
        
        quantity_mismatch = (comp.quantity != comp.quantity_web)
        value_mismatch = ((comp.market_value - comp.market_value_web).abs()/comp.market_value_web >= TOLERANCE*2)
        
        if quantity_mismatch.sum() > 0:
            print('Some quantities do not match in the front-end and the API')
            print(comp[quantity_mismatch])
            raise
            
        if value_mismatch.sum() > 0:
            print('Some market values deviate by more than 2% between the front-end and the API')
            print(comp[value_mismatch])
            raise
    
    @property
    def df_positions(self):
        '''Returns a DataFrame containing the positions in this portfolio
        
        Note: a copy is returned to avoid inadvertent modification
        
        Columns
        -------
            INDEX          : the ticker for the security
          - position_id    : unique identifier for the position
          - market_value
          - quantity
          - sub_type       : eTrade subtype (eg: "ETF")
          - lots_loss      : the sum of losses over all losing tax lots
          - lots_gain      : the sum of gains over all tax lots that experienced a gain
        '''
        
        if self._account_number is None:
            df = pd.DataFrame(eval("{'position_id': {'IEMG': '726822267037', 'VIG': '357525401455', 'VO': '304166308207', 'VTEB': '596183292174', 'VV': '747522072806', 'VEA': '480795814111', 'SUB': '982687778545', 'SCHA': '716346764725', 'SCHD': '532838711335', 'SCHF': '452905766374', 'SCHH': '408860214535', 'VWO': '756144736644'}, 'market_value': {'IEMG': 33403.7197, 'VIG': 28958.38, 'VO': 145373.56988096828, 'VTEB': 74972.838, 'VV': 470786.57, 'VEA': 41470.920000000006, 'SUB': 30534.57, 'SCHA': 59653.4757, 'SCHD': 13809.199104166666, 'SCHF': 251951.68, 'SCHH': 39863.2, 'VWO': 11938.037205202021}, 'quantity': {'IEMG': 743, 'VIG': 194, 'VO': 713, 'VTEB': 1570, 'VV': 2699, 'VEA': 1032, 'SUB': 297, 'SCHA': 1443, 'SCHD': 184, 'SCHF': 8117, 'SCHH': 2080, 'VWO': 318}, 'sub_type': {'IEMG': 'ETF', 'VIG': 'ETF', 'VO': 'ETF', 'VTEB': 'ETF', 'VV': 'ETF', 'VEA': 'ETF', 'SUB': 'ETF', 'SCHA': 'ETF', 'SCHD': 'ETF', 'SCHF': 'ETF', 'SCHH': 'ETF', 'VWO': 'ETF'}, 'lots_loss': {'IEMG': -283.448699999999, 'VIG': -178.71999999999989, 'VO': 0.0, 'VTEB': -1101.5119999999974, 'VV': 0.0, 'VEA': 0.0, 'SUB': -551.1618000000001, 'SCHA': 0.0, 'SCHD': 0.0, 'SCHF': 0.0, 'SCHH': 0.0, 'VWO': -721.1167194444447}, 'lots_gain': {'IEMG': 1022.7101999999986, 'VIG': 9816.759999999998, 'VO': 8279.99758096829, 'VTEB': 0.0, 'VV': 33691.91149999999, 'VEA': 5650.3730000000005, 'SUB': 0.0, 'SCHA': 3580.2272999999996, 'SCHD': 3022.5441041666663, 'SCHF': 22087.80650000002, 'SCHH': 2277.8079999999986, 'VWO': 131.79452464646496}}"))
            
            df.index.name = 'symbol'
            
            return df
        
        return self._df_positions.copy()

    @property
    def df_lots(self):
        '''Returns a DataFrame containing the lots in this portfolio
        
        Note: a copy is returned to avoid inadvertent modification
        
        Columns
        -------
          - position_id     : matches to a specific position in df_positions
          - position_lot_id : lot ID
          - symbol
          - acquired_date   : date acquired
          - price           : unit price when purchased
          - market_value    : total market value of the lot today
          - quantity        : quantity in lot
          - gain            : total gain (+ve) or loss (-ve) on lot
          
        
        '''
        
        if self._account_number is None:
            return pd.DataFrame(eval("{'position_id': {0: '726822267037', 1: '726822267037', 2: '726822267037', 3: '726822267037', 4: '726822267037', 5: '726822267037', 6: '726822267037', 7: '726822267037', 8: '726822267037', 9: '726822267037', 10: '726822267037', 11: '726822267037', 12: '357525401455', 13: '357525401455', 14: '357525401455', 15: '357525401455', 16: '357525401455', 17: '357525401455', 18: '357525401455', 19: '357525401455', 20: '357525401455', 21: '357525401455', 22: '357525401455', 23: '357525401455', 24: '357525401455', 25: '357525401455', 26: '304166308207', 27: '596183292174', 28: '747522072806', 29: '747522072806', 30: '747522072806', 31: '480795814111', 32: '480795814111', 33: '480795814111', 34: '982687778545', 35: '982687778545', 36: '982687778545', 37: '982687778545', 38: '716346764725', 39: '532838711335', 40: '532838711335', 41: '532838711335', 42: '532838711335', 43: '532838711335', 44: '532838711335', 45: '532838711335', 46: '532838711335', 47: '532838711335', 48: '532838711335', 49: '532838711335', 50: '532838711335', 51: '532838711335', 52: '532838711335', 53: '532838711335', 54: '532838711335', 55: '532838711335', 56: '532838711335', 57: '532838711335', 58: '532838711335', 59: '532838711335', 60: '532838711335', 61: '532838711335', 62: '532838711335', 63: '532838711335', 64: '452905766374', 65: '452905766374', 66: '452905766374', 67: '452905766374', 68: '452905766374', 69: '452905766374', 70: '452905766374', 71: '408860214535', 72: '756144736644', 73: '756144736644', 74: '756144736644', 75: '756144736644', 76: '756144736644', 77: '756144736644', 78: '756144736644', 79: '756144736644', 80: '756144736644', 81: '756144736644', 82: '756144736644', 83: '756144736644', 84: '756144736644'}, 'position_lot_id': {0: '494715020211', 1: '153710106948', 2: '458239829796', 3: '764195865158', 4: '264242557407', 5: '257906580532', 6: '578396236457', 7: '578644828386', 8: '670960862695', 9: '864488614669', 10: '752009792373', 11: '649921159609', 12: '750199044312', 13: '390663022467', 14: '425609790059', 15: '305436907790', 16: '364342641749', 17: '667878511468', 18: '182894445950', 19: '490331055411', 20: '487776486996', 21: '544316587884', 22: '483247261265', 23: '381035100674', 24: '483716176266', 25: '904050246804', 26: '949744016382', 27: '551653008295', 28: '661557656612', 29: '204056555571', 30: '385556933637', 31: '473343590757', 32: '879678242094', 33: '325409828856', 34: '534730837835', 35: '987003807048', 36: '567536607333', 37: '651605073186', 38: '208565799391', 39: '843706720455', 40: '642754115569', 41: '590561205819', 42: '408487450396', 43: '373708710124', 44: '475319989921', 45: '713170689212', 46: '887911157614', 47: '559380103729', 48: '702382404665', 49: '627342897305', 50: '662413151885', 51: '707220145888', 52: '858108193857', 53: '174875489499', 54: '787314557298', 55: '319299737082', 56: '274800664520', 57: '615211261741', 58: '186141264951', 59: '896794143646', 60: '664524074845', 61: '751074722370', 62: '114516286025', 63: '634988691499', 64: '601106673154', 65: '243063679730', 66: '237763463612', 67: '725976575893', 68: '386889783743', 69: '722773265977', 70: '598944924745', 71: '450055516710', 72: '932619240651', 73: '857502997220', 74: '421657810014', 75: '139232317419', 76: '374291266069', 77: '458367113725', 78: '734462947405', 79: '995822633829', 80: '420323379145', 81: '786293032406', 82: '633859224905', 83: '722531618829', 84: '236014707113'}, 'symbol': {0: 'IEMG', 1: 'IEMG', 2: 'IEMG', 3: 'IEMG', 4: 'IEMG', 5: 'IEMG', 6: 'IEMG', 7: 'IEMG', 8: 'IEMG', 9: 'IEMG', 10: 'IEMG', 11: 'IEMG', 12: 'VIG', 13: 'VIG', 14: 'VIG', 15: 'VIG', 16: 'VIG', 17: 'VIG', 18: 'VIG', 19: 'VIG', 20: 'VIG', 21: 'VIG', 22: 'VIG', 23: 'VIG', 24: 'VIG', 25: 'VIG', 26: 'VO', 27: 'VTEB', 28: 'VV', 29: 'VV', 30: 'VV', 31: 'VEA', 32: 'VEA', 33: 'VEA', 34: 'SUB', 35: 'SUB', 36: 'SUB', 37: 'SUB', 38: 'SCHA', 39: 'SCHD', 40: 'SCHD', 41: 'SCHD', 42: 'SCHD', 43: 'SCHD', 44: 'SCHD', 45: 'SCHD', 46: 'SCHD', 47: 'SCHD', 48: 'SCHD', 49: 'SCHD', 50: 'SCHD', 51: 'SCHD', 52: 'SCHD', 53: 'SCHD', 54: 'SCHD', 55: 'SCHD', 56: 'SCHD', 57: 'SCHD', 58: 'SCHD', 59: 'SCHD', 60: 'SCHD', 61: 'SCHD', 62: 'SCHD', 63: 'SCHD', 64: 'SCHF', 65: 'SCHF', 66: 'SCHF', 67: 'SCHF', 68: 'SCHF', 69: 'SCHF', 70: 'SCHF', 71: 'SCHH', 72: 'VWO', 73: 'VWO', 74: 'VWO', 75: 'VWO', 76: 'VWO', 77: 'VWO', 78: 'VWO', 79: 'VWO', 80: 'VWO', 81: 'VWO', 82: 'VWO', 83: 'VWO', 84: 'VWO'}, 'acquired_date': {0: datetime.date(2016, 12, 17), 1: datetime.date(2017, 1, 2), 2: datetime.date(2017, 1, 2), 3: datetime.date(2017, 3, 13), 4: datetime.date(2018, 12, 26), 5: datetime.date(2020, 4, 12), 6: datetime.date(2020, 4, 13), 7: datetime.date(2020, 4, 22), 8: datetime.date(2020, 4, 23), 9: datetime.date(2020, 4, 29), 10: datetime.date(2020, 4, 30), 11: datetime.date(2020, 5, 11), 12: datetime.date(2016, 10, 12), 13: datetime.date(2016, 10, 21), 14: datetime.date(2017, 5, 14), 15: datetime.date(2017, 5, 15), 16: datetime.date(2020, 6, 6), 17: datetime.date(2020, 6, 26), 18: datetime.date(2020, 6, 28), 19: datetime.date(2020, 7, 1), 20: datetime.date(2021, 4, 2), 21: datetime.date(2021, 4, 8), 22: datetime.date(2021, 4, 15), 23: datetime.date(2021, 5, 1), 24: datetime.date(2021, 10, 29), 25: datetime.date(2021, 11, 1), 26: datetime.date(2022, 10, 8), 27: datetime.date(2022, 10, 12), 28: datetime.date(2017, 5, 20), 29: datetime.date(2018, 8, 14), 30: datetime.date(2022, 10, 7), 31: datetime.date(2020, 4, 23), 32: datetime.date(2020, 5, 4), 33: datetime.date(2020, 5, 15), 34: datetime.date(2022, 7, 12), 35: datetime.date(2022, 7, 6), 36: datetime.date(2022, 8, 1), 37: datetime.date(2022, 9, 11), 38: datetime.date(2022, 10, 7), 39: datetime.date(2017, 3, 23), 40: datetime.date(2018, 5, 3), 41: datetime.date(2018, 12, 16), 42: datetime.date(2018, 12, 20), 43: datetime.date(2018, 12, 17), 44: datetime.date(2018, 12, 26), 45: datetime.date(2020, 3, 9), 46: datetime.date(2020, 3, 15), 47: datetime.date(2020, 3, 27), 48: datetime.date(2020, 3, 25), 49: datetime.date(2020, 12, 19), 50: datetime.date(2021, 1, 1), 51: datetime.date(2020, 12, 31), 52: datetime.date(2021, 1, 9), 53: datetime.date(2021, 1, 16), 54: datetime.date(2021, 2, 1), 55: datetime.date(2021, 2, 3), 56: datetime.date(2021, 1, 31), 57: datetime.date(2021, 2, 6), 58: datetime.date(2021, 2, 16), 59: datetime.date(2021, 2, 23), 60: datetime.date(2021, 3, 5), 61: datetime.date(2021, 3, 15), 62: datetime.date(2021, 3, 16), 63: datetime.date(2021, 3, 26), 64: datetime.date(2020, 3, 12), 65: datetime.date(2020, 3, 12), 66: datetime.date(2020, 10, 24), 67: datetime.date(2020, 10, 29), 68: datetime.date(2022, 10, 8), 69: datetime.date(2022, 10, 18), 70: datetime.date(2022, 10, 16), 71: datetime.date(2022, 10, 5), 72: datetime.date(2020, 3, 10), 73: datetime.date(2020, 3, 22), 74: datetime.date(2020, 3, 29), 75: datetime.date(2020, 6, 6), 76: datetime.date(2020, 6, 6), 77: datetime.date(2020, 6, 10), 78: datetime.date(2020, 6, 28), 79: datetime.date(2020, 6, 29), 80: datetime.date(2022, 7, 5), 81: datetime.date(2022, 7, 12), 82: datetime.date(2022, 8, 9), 83: datetime.date(2022, 9, 12), 84: datetime.date(2022, 10, 5)}, 'price': {0: 42.6989, 1: 42.54, 2: 42.5387, 3: 45.8386, 4: 46.01, 5: 42.08, 6: 42.89, 7: 42.8891, 8: 42.13, 9: 42.15, 10: 42.3098, 11: 43.22, 12: 81.85, 13: 82.23, 14: 90.97, 15: 90.29, 16: 117.36, 17: 117.12, 18: 115.21, 19: 117.45, 20: 150.04, 21: 150.71, 22: 152.08, 23: 152.63, 24: 164.77, 25: 164.81, 26: 192.2771, 27: 48.455, 28: 108.4359, 29: 129.4898, 30: 166.1999, 31: 34.7099, 32: 34.64, 33: 34.71, 34: 104.685, 35: 104.688, 36: 105.26, 37: 103.9199, 38: 38.8588, 39: 44.24, 40: 48.0, 41: 48.375, 42: 47.69, 43: 46.1, 44: 44.19, 45: 46.29, 46: 43.59, 47: 40.3575, 48: 42.77, 49: 62.8, 50: 63.84, 51: 63.78, 52: 63.32, 53: 66.4, 54: 65.08, 55: 63.61, 56: 64.7693, 57: 66.549, 58: 67.57, 59: 67.92, 60: 69.08, 61: 72.18, 62: 72.09, 63: 72.15, 64: 24.5798, 65: 23.3898, 66: 30.4397, 67: 30.2, 68: 28.495, 69: 28.29, 70: 28.8894, 71: 18.0699, 72: 33.73, 73: 32.46, 74: 33.22, 75: 40.1584, 76: 39.1, 77: 38.72, 78: 39.58, 79: 40.2, 80: 41.5597, 81: 41.5561, 82: 41.14, 83: 40.3295, 84: 36.8099}, 'market_value': {0: 13667.201599999999, 1: 314.7053, 2: 359.6632, 3: 10924.7697, 4: 2967.2213999999994, 5: 44.9579, 6: 44.9579, 7: 449.579, 8: 314.7053, 9: 269.7474, 10: 4001.2531000000004, 11: 44.9579, 12: 10896.71, 13: 149.27, 14: 149.27, 15: 11493.79, 16: 149.27, 17: 447.81000000000006, 18: 298.54, 19: 149.27, 20: 1044.89, 21: 1194.16, 22: 746.35, 23: 1044.89, 24: 298.54, 25: 895.6200000000001, 26: 145373.56988096828, 27: 74972.838, 28: 21803.75, 29: 20233.88, 30: 428748.94, 31: 41390.55, 32: 40.185, 33: 40.185, 34: 28067.13, 35: 616.86, 36: 616.8599999999999, 37: 1233.72, 38: 59653.4757, 39: 75.05, 40: 75.05, 41: 150.1, 42: 75.05, 43: 450.2999, 44: 1350.8999, 45: 1726.1499041666668, 46: 300.2, 47: 300.2, 48: 525.35, 49: 1576.05, 50: 225.1499, 51: 75.05, 52: 900.5999, 53: 600.4, 54: 900.5999, 55: 75.05, 56: 750.5, 57: 600.4, 58: 825.55, 59: 225.1499, 60: 150.1, 61: 75.05, 62: 825.5499083333334, 63: 975.6498916666667, 64: 2079.68, 65: 24707.84, 66: 2886.72, 67: 372.48, 68: 9591.36, 69: 155.2, 70: 212158.4, 71: 39863.2, 72: 375.4099090909091, 73: 37.541, 74: 337.86891, 75: 825.9019999999999, 76: 112.62290000000002, 77: 375.4099, 78: 37.541, 79: 375.4099, 80: 863.443, 81: 1614.2628805555553, 82: 1126.23, 83: 3303.6079, 84: 2552.787905555556}, 'quantity': {0: 304, 1: 7, 2: 8, 3: 243, 4: 66, 5: 1, 6: 1, 7: 10, 8: 7, 9: 6, 10: 89, 11: 1, 12: 73, 13: 1, 14: 1, 15: 77, 16: 1, 17: 3, 18: 2, 19: 1, 20: 7, 21: 8, 22: 5, 23: 7, 24: 2, 25: 6, 26: 713, 27: 1570, 28: 125, 29: 116, 30: 2458, 31: 1030, 32: 1, 33: 1, 34: 273, 35: 6, 36: 6, 37: 12, 38: 1443, 39: 1, 40: 1, 41: 2, 42: 1, 43: 6, 44: 18, 45: 23, 46: 4, 47: 4, 48: 7, 49: 21, 50: 3, 51: 1, 52: 12, 53: 8, 54: 12, 55: 1, 56: 10, 57: 8, 58: 11, 59: 3, 60: 2, 61: 1, 62: 11, 63: 13, 64: 67, 65: 796, 66: 93, 67: 12, 68: 309, 69: 5, 70: 6835, 71: 2080, 72: 10, 73: 1, 74: 9, 75: 22, 76: 3, 77: 10, 78: 1, 79: 10, 80: 23, 81: 43, 82: 30, 83: 88, 84: 68}, 'gain': {0: 686.7359999999986, 1: 16.92530000000002, 2: 19.35360000000003, 3: -214.01009999999908, 4: -69.43859999999992, 5: 2.877900000000004, 6: 2.0679000000000016, 7: 20.68800000000001, 8: 19.795299999999997, 9: 16.84740000000005, 10: 235.68089999999984, 11: 1.7379000000000033, 12: 4921.66, 13: 67.04, 14: 58.30000000000001, 15: 4541.459999999999, 16: 31.91000000000001, 17: 96.44999999999999, 18: 68.12000000000003, 19: 31.820000000000007, 20: -5.389999999999873, 21: -11.519999999999982, 22: -14.050000000000011, 23: -23.52000000000001, 24: -31.0, 25: -93.24000000000001, 26: 8279.99758096829, 27: -1101.5119999999974, 28: 8249.262500000003, 29: 5213.063200000001, 30: 20229.585799999983, 31: 5639.353, 32: 5.545000000000002, 33: 5.475000000000001, 34: -511.875, 35: -11.268, 36: -14.700000000000077, 37: -13.31880000000001, 38: 3580.2272999999996, 39: 30.809999999999995, 40: 27.049999999999997, 41: 53.349999999999994, 42: 27.36, 43: 173.69989999999996, 44: 555.4798999999999, 45: 661.4799041666668, 46: 125.83999999999999, 47: 138.76999999999998, 48: 225.95999999999995, 49: 257.25, 50: 33.62989999999999, 51: 11.269999999999996, 52: 140.75990000000002, 53: 69.2, 54: 119.63990000000001, 55: 11.439999999999998, 56: 102.80700000000002, 57: 68.0079999999999, 58: 82.27999999999997, 59: 21.38990000000001, 60: 11.939999999999998, 61: 2.8699999999999903, 62: 32.55990833333334, 63: 37.699891666666645, 64: 432.8334, 65: 6089.559199999999, 66: 55.82790000000019, 67: 10.080000000000041, 68: 786.4049999999999, 69: 13.75, 70: 14699.35100000002, 71: 2277.8079999999986, 72: 38.109909090909106, 73: 5.080999999999996, 74: 38.88891, 75: -57.58279999999998, 76: -4.67710000000001, 77: -11.790099999999995, 78: -2.0390000000000015, 79: -26.590100000000007, 80: -92.43009999999991, 81: -172.64941944444465, 82: -107.96999999999997, 83: -245.38810000000012, 84: 49.71470555555586}}"))
        
        return self._df_lots.copy()
    
    @property
    def cash(self):
        '''The amount of cash in this portfolio'''
        
        if self._account_number is None:
            return 40000
        
        return self._cash
    
    @property
    def recent_trades(self):
        '''Returns a dictionary containing securities bought and sold in the last 30 days
        
        Note: a copy is returned to avoid inadvertent modification
        
        The dictionary will contain two entires:
          - 'sold'   : a list of tickers for securities that have been sold in the last 30
                       days
          - 'bought' : same with bought
        '''
        
        if self._account_number is None:
            return {'sold'   : ['VNQ', 'MUB', 'ORAN', 'IJH', 'IVV', 'VIG', 'VEA', 'IJR', 'NOK'],
                    'bought' : ['VO', 'VWO', 'SCHF', 'VV', 'ORAN', 'SCHH', 'SCHA', 'NOK', 'VTEB']}
        
        return {'sold'   : list(self._recent_trades['sold']),
                'bought' : list(self._recent_trades['bought'])}

class Rebalancer:
    '''Rebalancing class; handles the re-balancing of a portfolio with tax loss harvesting
    
    This class is the powerhorse of this library; when it is initialized, it immediately
    prints out a summary of the portfolio and of any rebalancing required. It then exposes
    a rebalance() method which carires out the required trades.
    '''

    def __init__(self, account, conn = None, target_portfolio = None, MAX_LOSS_TO_FORGO = 0, MAX_GAIN_TO_SELL = 0, forced_buys = {}):
        '''Initialize the rebalancer and tax loss harvester
        
        This function will initialize the rebalancer; Rebalancer.rebalance() can then
        be called to carry out the trades
        
        Arguments
        ---------
          - account           : an Account object
          - conn              : and EtradeConnection object
          - target_portfolio  : a TargetPortfolio object
          - MAX_LOSS_TO_FORGO : parameter in the rebalancing; see docs for details
          - MAX_GAIN_TO_SELL  : parameter in the rebalancing; see docs for details
          - forced_buys       : this dictionary allows the user to by-pass the algorithm in deciding
                                which security of each asset class to BUY. If provided, this should be
                                a dictionary in which each key is the name of an asset class, and each
                                value is EITHER None (to signify this asset class should not be bought)
                                OR a string with an uppercase ticker for the security that should be
                                bought.
        '''
        
        print('Setting up rebalancer')
        
        # If we haven't provided a target portfolio, create a default one
        if target_portfolio is None:
            target_portfolio = TargetPortfolio()
            
            target_portfolio.add_assetclass(41, '1. US Large Cap',              ['IVV', 'SCHX', 'VV',   'VOO', 'IWB'      ],
                                                                                [1,     2,      3,      4,     None       ])
            target_portfolio.add_assetclass(11, '2. US Mid Cap',                ['IJH', 'VO',   'SCHM', 'IWR'             ],
                                                                                [1,     2,      3,      None              ])
            target_portfolio.add_assetclass(5,  '3. US Small Cap',              ['IJR', 'SCHA', 'VB',   'VXF', 'IWM'      ],
                                                                                [1,     2,      3,      None, None        ])
            target_portfolio.add_assetclass(23, '4. Int. Developed Mkts',       ['VEA', 'IEFA', 'SCHF', 'VEU'             ],
                                                                                [1,     2,      3,      4                 ])
            target_portfolio.add_assetclass(5,  '5. Int. Emerging Mkts',        ['VWO', 'IEMG', 'SCHE'                    ],
                                                                                [1,     2,      3                         ])
            target_portfolio.add_assetclass(5,  '6. Real Estate',               ['VNQ', 'SCHH', 'USRT', 'RWR'             ],
                                                                                [1,     2,      3,      None              ])
            target_portfolio.add_assetclass(3,  '7. Fixed Income Short Trm',    ['SUB', 'SHM'                             ],
                                                                                [1,     2                                 ])
            target_portfolio.add_assetclass(7,  '8. Fixed Income Mid+Long Trm', ['MUB', 'VTEB', 'TFI',  'ITM'             ],
                                                                                [1,     2,      3,      None              ])
            target_portfolio.validate()
        
        # Store variables
        # ---------------
        self._conn = conn
        self._account = account
        self._target_portfolio = target_portfolio
        self._MAX_LOSS_TO_FORGO = MAX_LOSS_TO_FORGO
        self._MAX_GAIN_TO_SELL = MAX_GAIN_TO_SELL
        
        # Note that this rebalancer has not yet been optimized
        self._optimized = False
        
        # Process the forced buys
        # -----------------------
        for a in forced_buys:
            # Make sure this forced buy is an asset class that exists
            if a not in self._target_portfolio.target_assetclasses:
                raise RebalancerError('The force buy specified does not exist in the target portfolio.')
        
            if forced_buys[a] is None:
                forced_buys[a] = {'security' : None, 'badness' : None}
            else:
                # Find the security and make sure it belonds to that asset class
                security = self._target_portfolio.find_security(forced_buys[a])
                
                if security['assetclass'] != a:
                    raise RebalancerError(f'The forced buy specified for asset class {a} was not part '
                                                'of that asset class in the target portfolio, but was '
                                               f'in fact part of asset class {security["assetclass"]}. '
                                                'Please fix.')
                
                forced_buys[a] = {'security' : forced_buys[a], 'badness' : security['badness']}
        
        # Store the forced buys
        self._forced_buys = forced_buys
        
        # Go through steps
        # ----------------
        print('Identifying securities to buy in each asset class')
        self._identify_buys()
        
        print('Identifying lots to sell in each asset class')
        self._identify_sells()
        
        print('Determining how much of each asset class to buy')
        self._identify_buy_amounts()
        
        # Print the results
        self._print_status()
        
    def _identify_buys(self):
        '''For each target asset class, identify the security to buy
        
        See the documentation for a description of the rules used to do this
        
        It creates a self._buys dictionary, in which each entry corresponds to one
        asset class, and is itself a dictionary with the following items
          - security      : the security that will be purchased in this asset class
          - badness       : the badness of this security in this asset class
          - current_price : the current price of the security chosen
        '''
        
        buys = {}
        
        # Go through every target asset class and find the security to buy
        for a_name, a in self._target_portfolio.target_assetclasses.items():
            if a_name in self._forced_buys:
                # If we have a specified forced buy, pick it
                buys[a_name] = self._forced_buys[a_name]
            else:
                # No such luck, we weren't provided a forced buy; find one
                
                # Start with a badness score of 1
                badness_score = 1
            
                while len(a.securities(badness_score)) > 0:
                    # Find all securities at this badness level that haven't recently been
                    # sold, and find their losses (if they are not in df_positions, they
                    # aren't in the portfolio and have therefore experienced losses of 0)
                    buy_options = [(s, 0 if s in self._account.recent_trades['bought'] else self._account.df_positions.lots_loss.get(s, 0))
                                       for s in a.securities(badness_score)
                                          if (s not in self._account.recent_trades['sold'])]
                    
                    # Sort by the loss in decreasing order (least loss to most loss); the
                    # "sorted" function is stable, so any ties will remain in the original,
                    # user-specified order.
                    buy_options = sorted(buy_options, key = lambda x : -x[1])
                    
                    # If the abs(loss) in the security with the smallest loss is <=
                    # MAX_LOSS_TO_FORGO, we're good. Else move to the next badness level
                    if (len(buy_options) > 0) and (np.abs(buy_options[0][1]) <= self._MAX_LOSS_TO_FORGO):
                        buys[a_name] = {'security' : buy_options[0][0], 'badness' : badness_score}
                        break
                    else:
                        badness_score += 1
        
        # Check whether we've identified a buy for every asset class
        if set(buys.keys()) != set(self._target_portfolio.target_assetclasses):
            raise RebalancerError('The rebalancer was not able to identify a security to buy '
                                    'for every asset class')
        
        # For every asset, find the current price
        for _, a in buys.items():
            if a['security'] is not None:
                if self._conn is None:
                    a['current_price'] = {'IEMG': 44.9579, 'VIG': 149.27, 'VO': 203.8899998330551, 'VTEB': 47.7534,
                                            'VV': 174.43, 'VEA': 40.185, 'SUB': 102.81, 'SCHA': 41.3399,
                                            'SCHD': 75.04999513134058, 'SCHF': 31.04, 'SCHH': 19.165,
                                            'VWO': 37.540997500635285, 'IVV': 383.39, 'SCHX': 45.075, 'VOO': 350.855,
                                            'IWB': 208.79, 'IJH': 236.5, 'SCHM': 65.64500000000001, 'IWR': 67.1,
                                            'IJR': 98.92, 'VB': 185.5, 'VXF': 135.18, 'IWM': 179.4, 'IEFA': 59.575,
                                            'VEU': 46.725, 'SCHE': 10.785, 'VNQ': 81.91499999999999, 'USRT': 49.065,
                                            'RWR': 87.025, 'SHM': 45.54, 'MUB': 102.015, 'TFI': 43.86, 'ITM': 43.76}[a['security']]
                else:
                    a['current_price'] = self._conn.get_current_price(a['security'])
                
        self._buys = buys
            
    def _identify_sells(self):
        '''Identify all tax lots to sell, and preview the transactions with eTrade
        
        This function will identify all tax lots that should be sold.
        
        It creates a self._sells dictionary, in which each entry corresponds to one
        security we will sell, and is itself a dictionary with the following items
          - lots               : a list of lots to sell for this security. Each entry will
                                 be a dictionary with two entries; position_lot_id (the lot
                                 ID) and quantity (the quantity of securities in that lot to
                                 sell)
          - preview_result     : the result of the eTrade trade preview that can be
                                 later used to place the order for this security's
                                 transactions
          - estimated_proceeds : the estimated proceeds from the sale of this security
                                 returned by the eTrade API preview; this is extracted
                                 from the preview result for convenience
          - sell_loss          : True if all losing lots of this security are being sold
          - sell_gain          : True if all winning lots of this security are being sold
        '''
        
        # Create a list of all lots we cannot sell, either because they were
        # bought in the last 30 days, or because we're about to buy them
        do_not_sell = ( self._account.recent_trades['bought'] + [b['security'] for b in self._buys.values()] )
        
        # Create a dictionary of sells; we will eventually remove any securities
        # that will not be sold
        sells = {s : {'lots' : [], 'sell_loss' : False, 'sell_gain' : False}
                                          for s in self._account.df_positions.index}
        
        # Identify losing lots we should sell
        # -----------------------------------
        for row_n, row in self._account.df_lots.iterrows():
            # If we can sell this security, and the lot experienced a loss, sell it!
            if (row.symbol not in do_not_sell) and (row.gain < 0):
                sells[row.symbol]['lots'].append({'position_lot_id' : row.position_lot_id,
                                                  'quantity'        : row.quantity})
                sells[row.symbol]['sell_loss'] = True
        
        # Identify winning lots we should sell
        # ------------------------------------
        
        # This one's a little more complicated; we're going to go through
        # every SECURITY, not lot, and check whether this security meets
        # three tests:
        #   - The security is not in the do_not_sell list
        #   - We have identified a security to buy in this asset class
        #   - The badness of the security is > the badness of the security
        #     we're buying in that asset class OR the security is not in our
        #     target portfolio
        # If we pass the test, then we look at the lots of that security in
        # ascending order of gain. Sell all the lots with gains until the
        # cumulative gain (not offset by losses) reaches MAX_GAIN_TO_SELL
        
        for row_n, row in self._account.df_positions.iterrows():
            symbol     = row.name
            total_gain = row.lots_gain
            assetclass = self._target_portfolio.find_security(symbol)['assetclass']
            badness    = self._target_portfolio.find_security(symbol)['badness'] 
            
            if (      (row.name not in do_not_sell)
                  and (    (assetclass == OTHER_NAME)
                        or (self._buys[assetclass]['security'] is not None) )
                  and (    (assetclass == OTHER_NAME)
                        or (badness is None)
                        or (badness > self._buys[assetclass]['badness']))):
                # We've identified a security for which we want to sell gaining lots
                # if the gain is small enough. Identify those lots
                gaining_lots = (self._account
                                    .df_lots
                                    .query(f'(symbol == "{symbol}") and (gain > 0)')
                                    .sort_values('gain', ascending=True))
                
                # Add the lots until the gains are >= MAX_GAIN_TO_SELL
                lots_to_sell = 0
                while gaining_lots.iloc[0:(lots_to_sell+1), :].gain.sum() < self._MAX_GAIN_TO_SELL:
                    lots_to_sell += 1
                    
                    if lots_to_sell == len(gaining_lots):
                        break
                
                sells[symbol]['lots'].extend(gaining_lots.iloc[0:lots_to_sell, :]
                                                         .apply(lambda r : {'position_lot_id' : r.position_lot_id,
                                                                            'quantity'        : r.quantity},
                                                                axis = 1)
                                                         .tolist())
                
                if lots_to_sell > 0:
                    sells[symbol]['sell_gain'] = True
        
        # Filter down to securities for which
        # we'll sell at least one lot
        # -----------------------------------
        sells = {s : sells[s] for s in sells if sells[s]['sell_loss'] or sells[s]['sell_gain']}

        # Preview the orders
        # ------------------
        for s in tqdm(sells):
            if self._conn is None:
                sells[s]['estimated_proceeds'] = self._account.df_lots[lambda x : x.position_lot_id.isin([l['position_lot_id'] for l in sells[s]['lots']])].market_value.sum()
            else:
                sells[s]['preview_result'] = (
                        self._conn
                            .execute_order(account_id_key = self._account._account_id_key,
                                           symbol         = s,
                                           price_type     = 'MARKET',
                                           order_action   = 'SELL',
                                           quantity       = sum([l['quantity'] for l in sells[s]['lots']]),
                                           lots           = sells[s]['lots'],
                                           preview_only   = True) )
                
                sells[s]['estimated_proceeds'] = -sells[s]['preview_result']['estimated_cost']
        
        self._sells = sells
        
    def _identify_buy_amounts(self):
        '''This function will calculate the amount of each  asset class we should buy
        
        The amounts will be calculated to get the final allocation as close as possible to the target
        asset class percentages. This will be done using a constrained quadratic program; see the
        documentation for an explanation of the algorithm used
        
        This functions adds a 'target_buy' entry to each dictionary in self._buys, describing the
        amount of that asset class that will be bought. It will then self self._optimized to True.
        '''
        
        # Find the total proceeds from sales, including free cash
        cash_from_sales = self._get_assetclass_table().expected_sales.sum()
        
        # Get the rows of the allocations table that will be in the target
        # portfolio
        df_assetclasses = self._get_assetclass_table().pipe(lambda x : x[x.target_perc.notnull()])
        
        # Identify the securities that we are optimizing over
        I = df_assetclasses.buy_security.notnull().to_numpy()
        N = sum(I)
        
        # Calculate the total size of the portfolio we're building with these
        # securities
        T = sum(df_assetclasses.expected_balance.to_numpy()[I]) + cash_from_sales
        
        # Calculate the target market values; if I does not include every
        # asset class, the way we should do this is a little bit ambiguous.
        # The most obvious way to do this is simply to scale the percentages
        # of the securities in I to sum to 100%
        chi = df_assetclasses.target_perc.to_numpy()
        chi = chi/sum(chi[I])
        chi = chi*T
        
        # Find the expected market values
        ell = df_assetclasses.expected_balance.to_numpy()
        
        # Start with an x equal to ell
        x = ell.copy()
        
        # Solve
        # -----
        
        # Begin by assuming all the mus are 0; set gamma accordingly
        mu = np.zeros(len(chi))
        gamma = (2/N) * (sum(chi[I]) - T)
        
        # Now calculate the x's
        x[I] = chi[I] - 0.5*gamma
        
        # Iterate until we meet primal feasibility; to ensure we don't run
        # forever, at a 1000 iteration limit
        i = 0
        while (sum(x[I] < ell[I]) > 0) and (i < 1000):
            # Find the indices that violate feasibility or are on the edge of
            # the feasible region
            J = (x[I] <= ell[I])
            
            Imj = I.copy()
            Imj[J] = False
            
            # Find the new gamma
            gamma = (2 / (N - sum(J))) * ( sum(chi[Imj]) + sum(ell[J]) - T )
            
            # Find the new mu_j
            mu[J] = 2*(ell[J] - chi[J]) + gamma
            
            # Calculate the x's
            x[I] = chi[I] + 0.5*(mu[I] - gamma)
            
            i += 1
        
        # Return
        # ------
        
        for a, x in zip(df_assetclasses.index, x):
            self._buys[a]['target_buy'] = x - df_assetclasses.expected_balance[a]
            
        self._optimized = True
        
    def _get_assetclass_table(self):
        '''This function returns a Pandas DataFrame summarizing the asset classes in our target portfolio
        
        The table will contain the following columns
          INDEX: the asset class in question
            - market_value           : the market value of all securities held in this asset class
            - target_perc            : the percentage of this asset class in the target portfolio
            - current_perc_exc_other : the current percentage of the portfolio comprising this security.
                                       Note this will ONLY include securities in asset classes that we
                                       want to include in the target portfolio; all other securities (and
                                       cash) will not be included in these percentage calculations.
            - expected_sales         : the expected proceeds from sales of securities in this asset class
            - expected_balance       : the expected balance of securities in this asset class after sales
            - buy_security           : the security we will be buying in this asset class
        
        In addition, if self._optimized is True (in other words, if we have already calculated the quantity
        of each asset we will be buying), the following additional columns will be output
            - target_buy     : the amount of that asset class we would like to buy
            - buy_quantity   : the number of securities to buy in that asset class to achieve the target_buy
                               (this will be rounded down to the closest integer)
            - expected_buy   : the amount of that asset class that *will* be bought after rounding down
            - final_balance  : the final balance of this asset class after purchases
            - final_perc     : the final percentage of the portfolio that will comprise this asset class
        
        '''
        
        # Step 1; get the positions table and target asset classes
        # --------------------------------------------------------
        df_positions        = self._account.df_positions
        target_assetclasses = self._target_portfolio.target_assetclasses
        
        # Step 2; classify each security into asset classes
        # -------------------------------------------------
        df_positions['assetclass'] = df_positions.apply(lambda x : self._target_portfolio
                                                                       .find_security(x.name)
                                                                       ['assetclass'],
                                                        axis = 1)

        # Step 3; add expected sales
        # --------------------------
        if len(self._sells) > 0:
            df_positions['expected_sales'] = ( pd.DataFrame(self._sells)
                                                 .transpose()
                                                 ['estimated_proceeds'] )
            df_positions['expected_sales'] = df_positions['expected_sales'].fillna(0)
        else:
            df_positions['expected_sales'] = 0
            
        # Step 4; group by asset class
        # ----------------------------
        df = df_positions.groupby('assetclass')[['market_value', 'expected_sales']].sum()
        
        # Re-order rows so that the "other" allocation is at the end
        df = df.loc[ [i for i in df.index if i != OTHER_NAME] + [OTHER_NAME], :]
        
        # Step 5; Calculate %s
        # --------------------
        df['target_perc'] = pd.DataFrame({i : [j.target] for i, j in
                                            target_assetclasses.items()}).transpose()[0]
        
        rows_exc_other = df.index != OTHER_NAME
        df['current_perc_exc_other'] = None
        df.loc[rows_exc_other, 'current_perc_exc_other'] = (
                            df.loc[rows_exc_other, 'market_value'] * 100
                                     / df[rows_exc_other].market_value.sum() )
        
        # Step 6; add the cash row
        # ------------------------
        free_cash = self._account.cash
        df.loc[CASH_NAME, :] = {'market_value'   : free_cash,
                                'expected_sales' : free_cash}
        
        # Step 7; add an "expected balance" column
        # ----------------------------------------
        df['expected_balance'] = df.market_value - df.expected_sales.fillna(0)
        
        # Step 8; add the "buy security" column
        # -------------------------------------
        df['buy_security'] = pd.DataFrame(self._buys).transpose()['security']
        
        # Step 9; re-order columns
        # ------------------------
        df = df[['market_value', 'target_perc', 'current_perc_exc_other',
                            'expected_sales', 'expected_balance', 'buy_security']]
        
        # Step 10; add target purchases if they exist
        # -------------------------------------------
        if self._optimized:
            # Add the target_buy to the DataFrame
            df['target_buy'] = pd.DataFrame(self._buys).transpose()['target_buy']
        
            # Add the current price to the DataFrame
            df['current_price'] = pd.DataFrame(self._buys).transpose()['current_price']
        
            # Calculate the buy quantity and expected buy
            df['buy_quantity'] = (df.target_buy / df.current_price).apply(lambda x : int(x) if pd.notnull(x) else np.nan)
            df['expected_buy'] = df.buy_quantity * df.current_price
        
            # Calculate the final balance
            df['final_balance'] = df['expected_balance'] + df['expected_buy'].astype(float).fillna(0)
            
            # Calculate the final percentage
            rel_rows = df.target_perc.notnull()
            df['final_perc'] = None
            df.loc[rel_rows, 'final_perc'] = df[rel_rows].final_balance*100/df[rel_rows].final_balance.sum()
            
            # Figure out the amount of cash left
            df.loc[CASH_NAME, 'final_balance'] = df.expected_sales.sum() - df.expected_buy.sum()
            
            # Re-order columns
            df = df[['market_value', 'target_perc', 'current_perc_exc_other',
                         'expected_sales', 'expected_balance', 'buy_security', 'target_buy',
                                'buy_quantity', 'expected_buy', 'final_balance', 'final_perc']]
            
        return df
        
    def _print_status(self):
        '''This function prints a summary of any rebalancing that will be carried out'''
        
        display(Markdown('# Preliminary warnings'))
        
        # Determine whether the markets are open; print a warning
        if market_open():
            display(Markdown('  - It seems markets are currently open. <span style="color:red">'
                             '    PLEASE DOUBLE CHECK THAT THIS IS INDEED TRUE, THE ALGORITHM WE '
                             '    USE IS NOT PERFECT.</span> If you run the re-balancing algorithm'
                             '    while the market is closed, the orders will be queued, and by '
                             '    the time they execute, prices might have changed.'))
        else:
            display(Markdown("  - <span style='color:red'>It looks like markets are currently "
                             "    closed. The rebalancer will therefore not let you place any "
                             "    orders. If it did, they would be queued, and would only "
                             "    execute once the market re-opens, at which point prices might "
                             "    have changed.</span> The algorithm we use to determine whether "
                             "    markets are open isn't foolproof; to override it, modify the "
                             "    `market_open` function in the code."))
        
        # Warn about re-invested dividends
        display(Markdown("  - <span style='color:red'>Make sure your portfolio is not set to "
                         "    automatically re-invest dividends.</span> If it is, you might "
                         "    inadvertently create a wash sale."))
                             
        display(Markdown('# Portfolio rebalancing report'))
        
        # ==============================
        # =   Summary by asset class   =
        # ==============================
        display(Markdown('### 1. Summary by asset classes'))
        
        display(Markdown('Each row in the table below corresponds to a specific asset class we need '
                         'in our final portfolio, and an extra row for all assets that are not in one '
                         'of these "target" asset classes. For each asset, it provides the following '
                         'details:\n\n'
                         '  - **Market value**: the current market value of assets in this class.\n\n'
                         '  - **Target %**: the target percentage of the final portfolio that '
                         '    should comprise this asset class.\n\n'
                         '  - **Current %**: the current percentage of the portfolio that comprises '
                         '    this asset class. Note that this does *not* include assets in the '
                         '    "Other" category, but *does* include cash.\n\n'
                         '  - **Expected sales**: the $ amount of securities in this asset class that '
                         '    we intend to sell to carry out tax loss harvesting. Further details are '
                         '    provided in the forthcoming sections.\n\n'
                         '  - **Expected balance**: the expected market value of securities in this '
                         '    asset class *after* any expected sales described in the previous column.\n\n'
                         '  - **Buy security**: the security we will be buying to replenish this asset '
                         '    class.\n\n'
                         '  - **Buy target**: the $ amount of the security in the previous column we '
                         '    would ideally want to buy. This will be determined using a constrained '
                         '    quadratic program; see the documentation.\n\n'
                         '  - **Buy quantity**: the number of shares of the security in question we will '
                         '    buy. This is obtained by dividing the previous column by the share price, '
                         '    and rounding down.\n\n'
                         '  - **Expected buy**: the expected $ amount of the security we will buy, '
                         '    calculated by multiplying the quantity in the previous column by the price '
                         '    of the security. This should be close to **Buy target**, but not exactly '
                         '    equal to it because the quantity will have been rounded down.\n\n'
                         '  - **Final balance**: the final market value of this asset class after sales '
                         '    and purchases.\n\n'
                         '  - **Final %**: the final percentage of the portfolio that will comprise this '
                         '    asset class, after sales and purchases.'))
        
        # Get the table        
        df_assetclasses = self._get_assetclass_table()
                
        # Format and display it
        dollar_formatter = lambda x : '' if x == 0 else f'${x:,.2f}'
        df_assetclasses.index.name = None
        display( df_assetclasses.style
                                .format(na_rep    = '',
                                        # Format the numbers
                                        formatter = {'market_value'           : dollar_formatter,
                                                     'target_perc'            : lambda x : f'{int(x)}%',
                                                     'current_perc_exc_other' : lambda x : f'{round(x,2)}%',
                                                     'expected_sales'         : dollar_formatter,
                                                     'expected_balance'       : dollar_formatter,
                                                     'target_buy'             : dollar_formatter,
                                                     'buy_quantity'           : lambda x : '' if x == 0 else int(x),
                                                     'expected_buy'           : dollar_formatter,
                                                     'final_balance'          : dollar_formatter,
                                                     'final_perc'             : lambda x : f'{round(x,2)}%'})
                                # Display the "other" and "cash" row in italics
                                .apply(lambda x : ['font-style: italic;'
                                                         if pd.isnull(x.target_perc)
                                                                 else None]*len(x), axis=1)
                                # Change the column names to something more legible
                                .format_index(lambda x : {'market_value'           : 'Market value',
                                                          'target_perc'            : 'Target %',
                                                          'current_perc_exc_other' : 'Current %',
                                                          'expected_sales'         : 'Expected sales',
                                                          'expected_balance'       : 'Expected balance',
                                                          'buy_security'           : 'Buy security',
                                                          'target_buy'             : 'Buy target',
                                                          'buy_quantity'           : 'Buy quantity',
                                                          'expected_buy'           : 'Expected buy',
                                                          'final_balance'          : 'Final balance',
                                                          'final_perc'             : 'Final %'}.get(x,x), 
                                              axis = 1)
                                # Color the sales column in red
                                .apply(lambda x : ['background:pink']*len(x), subset = 'expected_sales')
                                .apply_index(lambda x : np.where(x=='expected_sales','background:pink', None),
                                             axis = 1)
                                # Color the buy column in green
                                .apply(lambda x : ['background:lightgreen']*len(x), subset = ['target_buy', 'expected_buy'])
                                .apply_index(lambda x : np.where(x.isin(['target_buy', 'expected_buy']),
                                                                 'background:lightgreen',
                                                                 None),
                                             axis = 1)
                                # Add vertical lines
                                .apply(lambda x : ['border-left: 2px solid black']*len(x), subset = ['expected_sales', 'buy_security', 'final_balance'])
                                .apply_index(lambda x : np.where(x.isin(['expected_sales', 'buy_security', 'final_balance']),
                                                                 'border-left: 2px solid black',
                                                                 None),
                                             axis = 1)
        )
        
        display(Markdown('The graph below summarizes the current and final deviations from the '
                         'target portfolio'))
        
        ( df_assetclasses[df_assetclasses.target_perc.notnull()]
                         .assign(current_perc_over = lambda x : x.current_perc_exc_other - x.target_perc,
                                 final_perc_over   = lambda x : x.final_perc - x.target_perc)
                         [['current_perc_over', 'final_perc_over']]
                         .rename(columns = {'current_perc_over' : 'Current % over target',
                                           'final_perc_over'   : 'Final % over target'})
                         .plot(kind='bar', xlabel='', ylabel='% (+ve = over target)') )
        sns.despine()
        plt.show()
        
        # ====================================
        # =   Security-by-security summary   =
        # ====================================
        display(Markdown('### 2. Security-by-security summary'))
        
        display(Markdown('Each row below corresponds to one security, separated by target asset class. '
                         'Every security in the current portfolio will be listed in this table, whether '
                         ' it appears in the target portfolio or not.\n\n'
                         '  - Column legend\n\n'
                         '       * **Badness**: the user-specified badness of that security within its asset class.\n\n'
                         '       * **30 days**: whether this security was bought (`B`) or sold (`S`) in the last 30 days.\n\n'
                         '       * **Quantity**, **Market value**: the quantity of that security currently owned, and the current '
                         '         market value of those securities; any empty entries represent securities that are not owned.\n\n'
                         '       * **Lots loss**, **Lots gain**: the sum of all losses/gains for all losing/gaining tax lots for'
                         '         this security.\n\n'
                         '  - The security highlighted in <span style="background:lightgreen">green</span> in each section denotes '
                         '    the security that will be **bought** during rebalancing.\n\n'
                         '  - Any cells highlighted in <span style="background:pink">pink</span> denote assets for which at least some '
                         '    tax lots that will be **sold** during rebalancing. More details on these tax lots are provided in the '
                         '    next section.'))
        
        # Step 2a; construct the table
        # ----------------------------
        
        # First, construct a base DataFrame in which each row is a security (either
        # securities in the portfolio, or securities in the target allocations)
        #   - The name of the asset class the security is in
        #   - The symbol for the security
        #   - The badness score for the security in that asset class
        
        # Begin with all the securities in target allocations
        df = [{'assetclass':a_name, 'symbol':symbol, 'badness':badness}
                    for a_name, a in self._target_portfolio.target_assetclasses.items()
                        for symbol, badness in zip(a.securities(), a.badness_scores)]
        
        # Add any securities in the portfolio that haven't been added yet
        df.extend([{'assetclass':'Not in target portfolio', 'symbol':symbol, 'badness':None}
                                            for symbol in set(self._account.df_positions.index)
                                                                - set([i['symbol'] for i in df])])
        
        # Add cash
        df.append({'assetclass':'Not in target portfolio', 'symbol':'Cash', 'badness':None})
        
        # Convert to a DataFrame
        df = pd.DataFrame(df)
        
        # Merge in data about how much of the security we hold
        df = pd.merge(df,
                      self._account.df_positions,
                      on       = 'symbol',
                      how      = 'outer',
                      validate = 'one_to_one')
        
        # Add cash data
        df.loc[df.symbol == 'Cash', 'market_value'] = self._account.cash
        
        # Add data about whether this security was traded in the last 30 days
        df['30_days'] = ''
        for s in self._account.recent_trades['bought']:
            df.loc[df.symbol == s, '30_days'] += 'B'
        for s in self._account.recent_trades['sold']:
            df.loc[df.symbol == s, '30_days'] += 'S'
        
        # Sort the table
        df = df.sort_values(['assetclass', 'badness']).reset_index(drop=True)
        
        # Step 2b; prepare to format the table
        # ------------------------------------
        
        # One of the ways to format a DataFrame in Pandas is using lists with as
        # many entries as rows in which each element contains formatting
        # instructions for the relevant row. Begin by constructing these lists
        
        # First, create a lists that will create a bottom border on the last line
        # of every asset class
        style = 'border-bottom: 3px solid black'
        last_line_border = [style
                              if df.assetclass[i] != df.assetclass[i+1] else None
                                                  for i in range(len(df)-1)] + [style]
                    
        # Because of the way multi-indexes work, the bottom border needs to be
        # on the FIRST line of every asset class for the index; create that list
        first_line_border = [style] + [style if df.assetclass[i]
                                                   != df.assetclass[i-1] else None
                                                           for i in range(1, len(df))]        
        
        # We now need to determine how to format the lot gains and lot losses.
        # Losses will be in red and gains will be in green. Any lots that will
        # be sold will have a pink background
        loss_format = []
        for row_n, row in df.iterrows():
            this_format = []
            
            if self._sells.get(row.symbol, {}).get('sell_loss'):
                this_format.append('background:pink')
            
            loss_format.append(';'.join(this_format) or None)
        
        gain_format = []
        for row_n, row in df.iterrows():
            this_format = []
            
            if self._sells.get(row.symbol, {}).get('sell_gain'):
                this_format.append('background:pink')
            
            gain_format.append(';'.join(this_format) or None)        
        
        # Finally, identify any rows containing securities we will be BOUGHT;
        # these should be highlighted in yellow
        buy_format = np.where(df.symbol.isin([i['security'] for i in self._buys.values()]),
                                                                 'background:lightgreen', None)
        
        # Step 2c; set the index and select columns
        # -----------------------------------------
        df = df.set_index(['assetclass', 'symbol'])
        df = df[['badness', '30_days', 'quantity',
                    'market_value', 'lots_loss', 'lots_gain']]
        
        # Step 2d; style the table
        # ------------------------
        df = ( df.style
                 # Format the cells to get the numbers to look nice, and to
                 # show NA values as blanks
                 .format(na_rep    = '',
                         formatter = {'badness'      : lambda x : int(x),
                                      'quantity'     : lambda x : int(x),
                                      'market_value' : lambda x : f'${x:,.2f}',
                                      'lots_loss'    : lambda x : '' if x == 0 else f'${x:,.2f}',
                                      'lots_gain'    : lambda x : '' if x == 0 else f'${x:,.2f}'})
                 # Change the column names to something more legible
                 .format_index(lambda x : {'badness'      : 'Badness',
                                           '30_days'      : '30 days',
                                           'quantity'     : 'Quantity',
                                           'market_value' : 'Market value',
                                           'lots_loss'    : 'Lots loss',
                                           'lots_gain'    : 'Lots gain'}.get(x,x), axis=1)
                 # Rotate the "badness" and "30_days" column names 90 degrees
                 .apply_index(lambda x : np.where(x.isin(['badness', '30_days']),
                                                    'transform: rotate(180deg); '
                                                    'writing-mode:vertical-rl', None), axis=1)
                 # Format the lots_loss and lots_gain columns
                 .apply(lambda x : loss_format, subset='lots_loss')
                 .apply(lambda x : gain_format, subset='lots_gain')
                 # Format the buy rows
                 .apply_index(lambda x : buy_format if x.name == 1 else [None]*len(x))
                 .apply(lambda x : buy_format, axis=0)
                 # Add the lines separating the allocations
                 .apply(lambda x : last_line_border)
                 .apply_index(lambda x : first_line_border if x.name == 0 else last_line_border) )
        
        # Display the results
        display(df)
        
        # ====================
        # =   Lots to sell   =
        # ====================
        
        # Find all the lots that will be sold
        sell_lots = [l['position_lot_id'] for s in self._sells for l in self._sells[s]['lots']]
        df_sell_lots = ( self._account
                             .df_lots
                             [lambda x : x.position_lot_id.isin(sell_lots)]
                             [['symbol', 'position_lot_id', 'acquired_date', 'quantity', 'market_value', 'gain']]
                             .reset_index(drop=True) )
                
        display(Markdown('### 3. Specific lots to sell'))
        
        display(Markdown( 'The following summarizes the positions we will sell:\n\n'
                         f'  - Total lots: **{len(df_sell_lots)}**.\n\n'
                         f'  - Total harvested losses: **\\\${df_sell_lots.gain.sum():,.2f}** '
                         f'(including **\\\${df_sell_lots.query("gain>0").gain.sum():,.2f}** of '
                          'realized gains).\n\n'))
        
        if len(sell_lots) > 0:
            # First, create a lists that will create a bottom border on the last line
            # of every allocation
            style = 'border-bottom: 3px solid black'
            last_line_border = [style
                                  if df_sell_lots.symbol[i] != df_sell_lots.symbol[i+1] else None
                                     for i in range(len(df_sell_lots)-1)] + [style]
                  
            # Because of the way multi-indexes work, the bottom border needs to be
            # on the FIRST line of every allocation for the index; create that list
            first_line_border = [style] + [style if df_sell_lots.symbol[i]
                                                       != df_sell_lots.symbol[i-1] else None
                                                               for i in range(1, len(df_sell_lots))]        
            
            display(df_sell_lots.rename(columns={'symbol'          : 'Symbol',
                                                 'position_lot_id' : 'Lot ID'})
                                .set_index(['Symbol', 'Lot ID'])
                                .style.format(formatter = {'market_value' : lambda x : f'${x:,.2f}',
                                                           'gain'         : lambda x : f'${x:,.2f}'})
                                      .format_index(lambda x : {'acquired_date'   : 'Acquired date',
                                                                'quantity'        : 'Quantity',
                                                                'market_value'    : 'Market value',
                                                                'gain'            : 'Gain',
                                                                'allocation'      : 'Allocation'}.get(x,x), axis=1)
                                      .apply(lambda x : last_line_border)
                                      .apply_index(lambda x : first_line_border if x.name == 0 else last_line_border) )
            
        # ================
        # =  Next steps  =
        # ================
        
        display(Markdown('# Next steps'))
        
        display(Markdown('When you are ready to re-balance, run the `rebalance` method of '
                         'this object. It will first sell the lots above, and then immediately '
                         'buy the assets described above.'))
        
        display(Markdown("<span style='color:red'>You must run this function as quickly as "
                         "possible after first creating the rebalancing object, to ensure "
                         "prices do not shift between the time the rebalancing amounts are "
                         "calculated, and the time the trades are executed. If you wait too "
                         "long, the previewed transactions will expire and eTrade will not "
                         "let you carry out any transaction.</span>"))
    
    def rebalance(self):
        '''This function will rebalance the portfolio by placing buy/sell trades
        
        Note that the function will require affirmative confirmation from the developer to place
        trades; they will need to type "yes". This is to ensure no trades are inadvertently run
        if the notebook is run automatically.
        '''
        
        # Get the cell width
        cell_width = 127
        
        # Ensure the markets are open
        if not market_open():
            raise RebalancerError('The markets are currently closed; please try again when the markets '
                                  'are open.')
        
        # Get confirmation
        if input('This function will place trades; please type "yes", all lowercase, to proceed  ') != 'yes':
            print('No trades have been placed.')
            return
        
        print()
        print()
        print('###############')
        print('#   SELLING   #')
        print('###############')
        print()
        print('Security'.ljust(10) + 'Outcome'.ljust(15) + 'eTrade messages')
        print('-'*cell_width)
        
        # Sell the lots we need to sell
        # -----------------------------
        error = False
        sells = self._sells
        for s in sells:
            print(s.ljust(10), end='')
            
            try:
                sells[s]['trade_result'] = ( self._conn
                                                 .execute_order(account_id_key = self._account._account_id_key,
                                                                symbol         = s,
                                                                price_type     = 'MARKET',
                                                                order_action   = 'SELL',
                                                                quantity       = sum([l['quantity'] for l in sells[s]['lots']]),
                                                                lots           = sells[s]['lots'],
                                                                preview_only   = False,
                                                                preview_result = sells[s]['preview_result']['preview_result']) )
            except Exception as e:
                sells[s]['trade_result'] = {'messages': str(e), 'outcome':'ERROR', 'error':e}
            
            print( sells[s]['trade_result']['outcome'].ljust(15), end='' )
            
            message_width = cell_width - 25
            messages = sells[s]['trade_result']['messages']
            print(('\n' + ' '*25).join([messages[(i*message_width):((i+1)*message_width)] for i in range(int(len(messages)/message_width)+1)]))
            print()
            
            if sells[s]['trade_result']['outcome'] != 'PLACED':
                error = True
        
        if error:
            raise RebalancerError('Some of the sell lots could not be sold; please check what '
                                  'happened. Just in case, I will *not* proceed with purchases, '
                                  'so you may be left with a large amount of cash in your account. '
                                  'Please triple check.')
        
        # Buy the lots we need to buy
        # ---------------------------
        
        print('##############')
        print('#   BUYING   #')
        print('##############')
        print()
        
        first_col_len = max(11, max(self._get_assetclass_table()
                                        [lambda x : x.buy_quantity.notnull()]
                                        .apply(lambda x : f'{x.name} + ({int(x.buy_quantity)} units of {x.buy_security})',
                                               axis = 1)
                                        .str
                                        .len()) + 3)
        
        print('Asset class'.ljust(first_col_len) + 'Outcome'.ljust(15) + 'eTrade messages')
        print('-'*cell_width)
        
        error = False
        for row_n, row in self._get_assetclass_table()[lambda x : x.buy_security.notnull()].iterrows():
            this_buy = self._buys[row.name]
            
            if pd.notnull(row.buy_quantity) and (row.buy_quantity > 0):
                print(f'{row.name} ({int(row.buy_quantity)} units of {row.buy_security})'.ljust(first_col_len), end='')
                
                try:
                    this_buy['trade_result'] = ( self._conn
                                                     .execute_order(account_id_key = self._account._account_id_key,
                                                                    symbol         = row.buy_security,
                                                                    price_type     = 'MARKET',
                                                                    order_action   = 'BUY',
                                                                    quantity       = int(row.buy_quantity),
                                                                    preview_only   = False) )
                except Exception as e:
                    this_buy['trade_result'] = {'messages': str(e), 'outcome':'ERROR', 'error':e}
                
                print( this_buy['trade_result']['outcome'].ljust(15), end='' )
                
                message_width = cell_width - first_col_len - 15
                messages = this_buy['trade_result']['messages']
                print(('\n' + ' '*(first_col_len + 15)).join([messages[(i*message_width):((i+1)*message_width)] for i in range(int(len(messages)/message_width)+1)]))
                print()
                
                if this_buy['trade_result']['outcome'] != 'PLACED':
                    error = True
                
                # Pause to make sure the trade has time to execute
                time.sleep(3)
            
        if error:
            raise RebalancerError('Some of the buys could not be carried out. Please '
                                  'triple check the state of the portfolio.')