TL;DR: see the [`sample_output.html`](https://htmlpreview.github.io/?https://github.com/danguetta/rebalancer/blob/main/sample_output.html) file in the repo for an example of what this code can do with a sample portfolio (click on the file name to preview the HTML file directly).

# Etrade Portfolio Rebalancer with tax-loss harvesting

This repo contains code required to rebalance and harvest tax losses in a simple portfolio, using the [eTrade API](https://developer.etrade.com/home).

  - The code first allows the developer to specify a target portfolio, comprising a number of asset classes, each with a target percentage. For example, the target portfolio might comprise three asset classes - `US Large Cap`, `Real Estate`, and `Fixed income`, and target for 60% of the portfolio to comprise the first asset class, 30% the second, and 10% the third.
  - For each asset class, the developer specifies securities that can be bought to own this asset class. For example, to own `US Large Cap`, the portfolio might buy one or more of `IVV`, `SCHX`, `VV`, or `VOO`. The code allows these securities to be ranked in order of preference; for example, `IVV` might be preferred over `SCHX` to satisfy the `US Large Cap` allocation.
  - Every time the script is run, it will obtain the current state of the portfolio using the eTrade API, and then automatically carry out buys and sells to meet the following aims
      - End with a portfolio in which the percentage of each asset class owned is as close as possible to the target
      - Sell as many losing positions as possible to harvest tax losses, and replace them with an alternative security in that asset class, all while avoiding wash sales (see [this link](https://www.bogleheads.org/wiki/Tax_loss_harvesting) for an introduction to tax loss harvesting).
      - For each asset class, prioritize *higher* preference securities over lower ones (in the example from the previous bullet, we would prefer to buy `IVV` over `SCHX`). 

As a side benefit, the code also provides a convenient interface to access the eTrade API for common opperations, and code to display the current state of a portfolio in a user-friendly fashion.

If this sounds very similar to what RoboAdvisors like [Wealthfront](https://www.wealthfront.com/) or [Betterment](https://www.betterment.com/) do, this is no coincidence - I started on these platforms, and then developed this script as a way to transition away from them and do this rebalancing myself.

# Quickstart guide

Begin by importing the rebalancer code.

```import rebalancer as u```

You may need to install any missing libraries first; in particular, you will need
  - The `holidays` library
  - Version 1.5.1 or later of `pandas`
  - Version 3.0.0 or later of `jinja2`

## Connecting to eTrade

If you're not quite ready to connect to your live eTrade portfolio, you can experiment with a sample portfolio; jump straight to the [downloading account data](#downloading-account-data) section.

To connect to the eTrade API, first get an API key by logging in to your account [here](https://us.etrade.com/etx/ris/apikey). Once you have obtained your consumer key and consumer secret, paste them into a configuration file, following the format of the `sample_config.ini` file in this repo.

Then, connect to the eTrade API by creating an `EtradeConnection` object

```conn = u.EtradeConnection(config_file, log_file)```

Use the following arguments to the constructor:

  - `config_file` (string) : the path to the configuration file containing the consumer key and consumer secret
  - `log_file` (string) : every time a request is transmitted to the eTrade API, it will be logged. This string will determine *where* this logging will happen.
      - If it is equal to `None`, the logs will be printed to a file named using today's date and time in a `logs` folder
      - If it is equal to `'screen'`, the logs will be printed to the Jupyter notebook
      - If it is equal to any other string, the logs will be printed to a file with that name

As soon as you create the object, a browser will be launched that will allow you to log in to the eTrade API; you will need to copy the authorization code from that browser window, and paste it into the Jupyter notebook.

## Downloading account data

Once the connection is established, the next step is to download portfolio details by creating an `Account` object.

If you have *not* connected to the eTrade API and would like to use the sample portfolio, simply initialize the object with no arguments:

```account = u.Account()```

Otherwise, use

```account = u.Account(account_number, conn, validation_folder)```

Use the following argumens to the constructor:

  - `account_number` (string) : the account number to download (each eTrade login can be associated with multiple accounts; find the account number by logging in to the eTrade front-end, and clicking on "show number" next to the account in question).
  - `conn` : an EtradeConnection object, obtained in the [previous section](#connecting-to-etrade).
  - `validation_folder` (string, optional) : during my testing, I found one instance in which the data downloaded through the API did not accurately reflect the portfolio. To guard against this eventuality, the code allows you to download a CSV describing the current portfolio from the eTrade front-end (click on "View full portfolio" on the overview page, and click on the down arrow at the top-right-hand corner of the portfolio page), and to compare this CSV to the API downloads. To do this, simply supply the name of the folder in which you store these front-end downloads; the script will compare the API downloads to the last file in that folder alphabetically by file name (so for example, naming files using the convention YYYY-MM-DD HH-MM.csv will always lead the last file to be the most recent one). If any of the assets or quantities in the CSV do not match the API downloads, an error will be thrown. Also, if any of the market values diverge by more than TOLERANCE (a constant defined at the top of the module), an error will be thrown. This is added in as an extra check. No check will be done if this argument is None
  
As soon as the object is created, the portfolio will be downloaded from eTrade.

## Specifying the target portfolio

The code comes pre-loaded with a default target portfolio, comprising the following allocations:

  - `US large cap` (41%), comprising `IVV`, `SCHX`, `VV`, `VOO`, and `IWB`, in that order of preference (except for `IWB`, which will be counted as part of this asset class if it is already in the portfolio, but will never be bought)
  - `US mid cap` (11%), comprising `IJH`, `VO`, `SCHM`, and `IWR`, in that order of preference (except for `IWR`, which will be counted as part of this asset class if it is already in the portfolio, but will never be bought)
  - `US small cap` (5%), comprising `IJR`, `SCHA`, `VB`, `VXF`, and `IWM`, in that order of preference (except for `VXF` and `IWM`, which will be counted as part of this asset class if it is already in the portfolio, but will never be bought)
  - `International developed markets` (23%), comprising `VEA`, `IEFA`, `SCHF`, and `VEU`, in that order of preference
  - `International emerging markets` (5%), comprising `VWO`, `IEMG`, and `SCHE`, in that order of preference
  - `Real estate` (5%), comprising `VNQ`, `SCHH`, `USRT`, and `RWR`, in that order of preference (except for `RWR`, which will be counted as part of this asset class if it is already in the portfolio, but will never be bought)
  - `Fixed income short term` (3%), comprising `SUB`, and `SHM`, in that order of preference
  - `Fixed income mid and long term` (7%), comprising `MUB`, `VTEB`, `TFI`, and `ITM`, in that order of preference (except for `ITM`, which will be counted as part of this asset class if it is already in the portfolio, but will never be bought)

If you would like to use this default portfolio, skip to the [next section](#calculating-rebalancing-amounts).

If you could like to specify your own allocation, begin by creating a target portfolio

```target_portfolio = TargetPortfolio()```

Then, add each asset class as follows

```target_portfolio.add_assetclass(target, name, securities, badness_scores)```

This function takes the following arguments:

  - `target` (integer) : the percentage of the portfolio that should comprise this asset class, expressed as an integer between 0 and 100 (for example, `41` for the first asset class above)
  - `name` (string) : the name of this asset class (for example `1. US Large Cap` for the first asset class above); you might want to include a number before the name to make sure the asset classes are sorted in the right order.
  - `securities` (list of integers) : a list containing the tickers in this asset class (for example, `['IVV', 'SCHX', 'VV', 'VOO', 'IWB']` for the first asset class above)
  - `badness_scores` (list of integers) : a list containing the "badness scores" for the securities above; the lowest badness score should be 1, and will indicate the security or securities that are most preferred for this asset class. Larger badness scores, indicating less preferred securities, should be consecutive integers. To indicate that a security in the portfolio should count as part of this asset class but shoudl never be bought, the badness score should be `None`. (For example, for the first asset class above, this list should be `[1, 2, 3, 4, None]`)

Once you have specified all target asset classes, you should run

```target_portfolio.validate()```

This will check that the targets sum to 100, and that no securities overlap between asset classes. You will not be able to use the target portfolio before it has been validated, and once the target portfolio has been validated, no new asset classes can be added to it.

## Calculating rebalancing amounts

We are now ready to initialize the rebalancer, as follows:

```rebalancer = u.Rebalancer(account, conn, target_portfolio, MAX_LOSS_TO_FORGO, MAX_GAIN_TO_SELL, forced_buys)```

The constructor takes the following arguments:

  - `account` : an `Account` object, created above (this object might be using the sample portfolio)
  - `conn` : an `EtradeConnection` object, created above; if you are using the sample portfolio and not connecting to the eTrade API, this can be set to `None` (its default value)
  - `target_portfolio` : a `TargetPortfolio` object, created above; to use the sample portfolio, simply set this to `None` (its default value)
  - `MAX_LOSS_TO_FORGO` (positive float) : a parameter in the [rebalancing algorithm](#the-rebalancing-algorithm). This comes in to play when the most preferred security in an asset class has experienced a loss. Because this is the most preferred security, we'd like to buy it, but this means we can't also sell it to harvet the loss. If magnitude of the loss is less than or equal to this parameter, we will forgo the loss and buy the security. If not, we will sell this one and buy a less desirable security. Default value is 0.
  - `MAX_GAIN_TO_SELL` (positive float) : a parameter in the [rebalancing algorithm](#the-rebalancing-algorithm). This comes into play when the portfolio contains a less desirable security in a given asset class that has experienced a gain. Because this is a less preferred security, we'd like to sell it and buy a more preferred security instead. Unfortunately, this would mean realizing a tax gain. If the magnitude of the gain that would result from selling the *entire* position of a security is less than or equal to this parameter, it will be sold. If not, it will not. Default value is 0.
  - `forced_buys` : the [rebalancing algorithm](#the-rebalancing-algorithm) will attempt to automatically pick the security to buy for each asset class. In some cases, you might want to *force* the algorithm to buy a specific security. To do this, provide a dictionary here in which each key is an asset class (matching the asset class name in the target portfolio) and each value is EITHER a string with a security ticker (in which case this security will be bought to fulfil this asset class) OR `None` (in which case this security will not be bought). Default value is `{}` (i.e., the algorithm picks everything). A few important warnings
     - The forced buy security *must* be in the relevant asset class in the target portfolio.
     - If you specify a forced buy, the algorithm will not check whether buying that security will result in a wash sale; check carefully.

As soon as the object is created, the full rebalancing amounts will be calculated, and any sell orders will be previewed (this requires a connection to the eTrade API; if you are using a sample portfolio, this step will be skipped).

The notebook will then print out a full summary of the current portfolio and all trades that will be carried out; 

## Executing trades

The final step is to execute trades; this will, of course, not be available if you are using the sample portfolio. To do this, simply run

```rebalancer.rebalance()```

You will be asked to type `yes` before any trades are placed; this is to ensure you do not inadvertently place trades when just running a notebook from top-to-bottom.

Note that you must run this function *soon* after creating the rebalancer object to ensure prices do not shift between the time the rebalancing amounts are calculated, and the time the trades are executed.

# The rebalancing algorithm

This section describes the rebalancing algorithm in detail. There are three steps - the [first](#identifying-buys) is deciding what security to buy for each asset class. The [second](#identifying-tax-lots-to-sell) is to decide what tax lots to sell. The [last](#calculating-buy-quantities) is to decide how much of each asset class to buy.

## Identifying buys

For each asset class, we first check whether it was included in the `forced_buys` dictionary. If it is, use the security specified there as the *buy* for that asset class. If not, identify a *buy* security as follows:

  1. First, identify any securities that have been sold in the last 30 days; exclude these from our consideration set to avoid wash sales
  2. Begin by looking at all securities in that asset class with a badness score of 1
      - For each of these securities, identify the total loss experienced by this security; we will want to buy the security with the *smallest* absolute loss (ideally a loss of 0) to make sure we do not forgo the ability to harvest that loss. If a security has been *bought* in the last 30 days, set the total loss of that security to 0, since we wouldn't be allowed to sell it anyway.
      - Find the security with the *smallest* absolute loss (breaking ties based on the order in which the securities were added to the target portfolio).
           - If that security has experienced an absolute loss less than or equal to the `MAX_LOSS_TO_FORGO` parameter, designate it as our "buy" security for this asset class, and move on to the next asset class.
           - If not, repeat step (2) with all securities with a badness score of 2
  3. If all securities in that asset class have been exhausted without identifying a buy, throw an error
  
## Identifying tax lots to sell

Having identified the asset the buy in each asset class, we can identify the tax lots we should sell.

Begin by identifying all *losing* tax lots to sell. Go through every tax lot - if the following three conditions are met, designate this tax loss as a "sell":
  - The tax lot has experienced a loss
  - The security in this tax lot has not been bought in the last 30 days
  - The security in this tax lot has not been designated as a "buy" in the previous step

Next, identify all *gaining* tax lots to sell. To do this, go through every security, and designate the *entire* position of that security as a "sell" if the following conditions are met:
  - The sum of all gains on all gaining tax lots of that security are less than or equal to the `MAX_GAIN_TO_SELL` parameter
  - The badness score of that security is greater than the badness score of the security designated as a buy for its asset class in the previous step
  - The security has not been bought in the last 30 days
  - The security has not been designated as a "buy" in the previous step

## Calculating buy quantities

We are now ready to identify how much of each asset class to purchase.

### Notation

  - Let $\mathcal{I}$ denote the set of asset classes we intend to buy, with $N = |\mathcal{I}|$ (this will usually contain every asset class, unless we specifically used the `forced_buys` parameter in the rebalancer to exclude an asset class from our purchases)
  - Let $\ell_i$ denote the market value of securities of asset class $i$ that are currently held (*after* any sales described in the [previous section](#identifying-tax-lots-to-sell)).
  - Let $T$ denote the amount of free cash available to invest (including cash from any sales in the previous section) *plus* $\sum_{i \in \mathcal{I}} \ell_i$. In other words, $T$ will be the total target market value of all securities in $\mathcal{I}$ after we're done with our round of purchasing.
  - Let $\chi_i$ denote the target market value of asset class $i$ in our portfolio; this is simply equal to $T$ multiplied by the target percentage of this asset class in the target portfolio.
  
### The optimization problem

Let $x_i$ denote the final market value of asset class $i$ after our purchasing. We seek to determine this by solving

$$
    \begin{array}{rlll}
        \min_{x_i : i \in \mathcal{I}} & & \sum_{i \in \mathcal{I}} (x_i - \chi_i)^2 \\
        \mathrm{s.t.}     & & \sum_{i \in \mathcal{I}} x_i = T \\
                          & & x_i \geq \ell_i & \forall i \in \mathcal{I}
    \end{array}
$$

In other words, we minimize the L2-norm of the gap between our purchase and our target subject to the budget constraint, and subjet to the fact we can only _increase_ a position, not decrease it.

### The KKT conditions

Associating the Lagrange multiplier $\gamma \in \mathbb{R}$ to the first constraint, and the set of multipliers $\mu \in \mathbb{R}^N$ with $\mu_i \geq 0$ to the second constraint. The Lagrangian for this optimization problem is then

$$ \mathcal{L}(x, \mu, \gamma) = \sum_{i \in \mathcal{I}} (x_i - \chi_i)^2 + \gamma\left( \sum_{i \in \mathcal{I}} x_i - T \right) + \sum_{i \in \mathcal{I}} \mu_i(\ell_i - x_i) $$

The [KKT conditions](https://en.wikipedia.org/wiki/Karush%E2%80%93Kuhn%E2%80%93Tucker_conditions) are then:

  1. **Stationarity**: $2(x_i - \chi_i) + \gamma - \mu_i = 0 \Rightarrow x_i = \chi_i + \frac{1}{2}(\mu_i - \gamma)$ for all $i \in \mathcal{I}$.
  2. **Primal feasibility**: $\sum_{i \in \mathcal{I}} x_i = T$, and $x_i \geq \ell_i$ for all $i \in \mathcal{I}$.
  3. **Dual feasibility**: $\mu_i \geq 0$ for all $i \in \mathcal{I}$.
  4. **Complementary slackness**: $\mu_i (\ell_i - x_i) = 0$ for all $i \in \mathcal{I}$.

### Solution technique

#### Step 1

We begin by assuming all of the $\mu_i$ are 0. Combining the stationarity condition and the first primal feasibility condition, we get

$$ \gamma = \frac{2}{N} \left( \sum_{i \in \mathcal{I}} \chi_i - T \right) $$

We can then use the stationarity condition to calculate each $x_i$ from $\gamma$. If the resulting $x_i$ meet primal feasibility (i.e., if $x_i \geq \ell_i$ for all $i \in \mathcal{I}$), then we're done - we've found the optimal solution.

(Incidentally, this solution is equivalent to taking each target $\chi_i$ and reducing or increasing each by the _same_ amount to ensure the final portfolio meets our target.)

#### Step 2

If we find that some of the $x_i$ _violate_ primal feasibility (i.e., if $x_i < \ell_i$ for one or more $i$), we need to fix them.

Let $\mathcal{J}$ be the set of asset classes for which the current allocation violates primal feasibility. We need to increase all the $\mu_j$ for $j \in \mathcal{J}$ to increase those $x_j$ and make them feasible. Because these multipliers will now be greater than 0, complementary slackness will require $x_j=\ell_j$ for all $j \in \mathcal{J}$. Thus, taking our expression for $x_j$ from stationarity and setting $x_j=\ell_j$, we get

$$ \begin{array}{ccc} \mu_j = 2(\ell_j - \chi_j) + \gamma & & \forall j \in \mathcal{J} \end{array} $$

Let $\mathcal{I} - \mathcal{J} = \lbrace i : i \in \mathcal{I}, i \notin \mathcal{J} \rbrace$. We can once again combine the stationarity condition, the first part of primal feasibility, and the fact that $\mu_i = 0$ for all $i \in \mathcal{I} - \mathcal{J}$ to get

$$ \gamma = \frac{1}{N} \left( 2\sum_{i \in \mathcal{I}} \chi_i + \sum_{j \in \mathcal{J}} \mu_j - 2T \right) $$

Combining the last two statements and solving, we get

$$ \gamma = \frac{2}{N - |\mathcal{J}|} \left( \sum_{i \in \mathcal{I} - \mathcal{J}} \chi_i + \sum_{j \in \mathcal{J}} \ell_j - T \right) $$

We can use this expression to calculate $\gamma$, and then use that to calculate each of the $\mu_j$ for $j \in \mathcal{J}$. We then repeat the process, updating $\mathcal{J}$ at each step until primal feasibility is met.

# Limitations

In my mind, the main limitation of this algorithm is that it treats the rebalancing problem as a single-period problem. In reality, you are likely to carry out this rebalancing not just once, but with some degree of regularity. There may be ways to exploit this to result in better tax-loss harvesting, or a more desirable portfolio. For example, if an asset is trending downwards, you might choose *not* to buy it, so that you can reserve the right to sell it in a few days without incurring a wash sale. Expressing the problem as a Markov decision process rather than as a static problem would allow me to capture some of these opportunities. It would also, however, we much harder, and would require some distribution assumptions on the transitions between various parts of the state space.

# License

This material is shared under a [CC BY-NC-SA 4.0 license](https://creativecommons.org/licenses/by-nc-sa/4.0/), though if you find this is too restrictive for what you're trying to do, drop me a line; I'm not attached to it.