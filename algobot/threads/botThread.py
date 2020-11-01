import traceback
import helpers
import algobot

from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, pyqtSlot
from telegram.error import InvalidToken

from data import Data
from enums import LIVE, SIMULATION
from realtrader import RealTrader
from simulationtrader import SimulationTrader
from telegramBot import TelegramBot


class BotSignals(QObject):
    started = pyqtSignal(int)
    simulationActivity = pyqtSignal(str)
    liveActivity = pyqtSignal(str)
    updated = pyqtSignal(int, dict)
    finished = pyqtSignal()
    error = pyqtSignal(int, str)


class BotThread(QRunnable):
    def __init__(self, caller: int, gui: algobot.Interface):
        super(BotThread, self).__init__()
        self.signals = BotSignals()
        self.gui = gui
        self.caller = caller
        self.trader = None

    def handle_telegram_bot(self):
        """
        Attempts to initiate Telegram bot.
        """
        try:
            gui = self.gui
            if gui.telegramBot is None:
                apiKey = gui.configuration.telegramApiKey.text()
                gui.telegramBot = TelegramBot(gui=gui, apiKey=apiKey)
            gui.telegramBot.start()
            self.signals.liveActivity.emit('Started Telegram bot.')
        except InvalidToken:
            self.signals.liveActivity.emit('Invalid token for Telegram. Please recheck credentials in settings.')

    def initialize_lower_interval_trading(self, caller, interval):
        """
        Initializes lower interval trading data object.
        :param caller: Caller that determines whether lower interval is for simulation or live bot.
        :param interval: Current interval for simulation or live bot.
        """
        sortedIntervals = ('1m', '3m', '5m', '15m', '30m', '1h', '2h', '12h', '4h', '6h', '8h', '1d', '3d')
        gui = self.gui
        if interval != '1m':
            lowerInterval = sortedIntervals[sortedIntervals.index(interval) - 1]
            if caller == LIVE:
                self.signals.liveActivity.emit(f'Retrieving data for lower interval {lowerInterval}...')
                gui.lowerIntervalData = Data(lowerInterval)
                self.signals.liveActivity.emit('Retrieved lower interval data successfully.')
            else:
                self.signals.simulationActivity.emit(f'Retrieving data for lower interval {lowerInterval}...')
                gui.simulationLowerIntervalData = Data(lowerInterval)
                self.signals.simulationActivity.emit("Retrieved lower interval data successfully.")

    def create_trader(self, caller):
        gui = self.gui
        if caller == SIMULATION:
            symbol = gui.configuration.simulationTickerComboBox.currentText()
            interval = helpers.convert_interval(gui.configuration.simulationIntervalComboBox.currentText())
            startingBalance = gui.configuration.simulationStartingBalanceSpinBox.value()
            self.signals.simulationActivity.emit(f"Retrieving data for interval {interval}...")
            gui.simulationTrader = SimulationTrader(startingBalance=startingBalance,
                                                    symbol=symbol,
                                                    interval=interval,
                                                    loadData=True)
            self.signals.simulationActivity.emit("Retrieved data successfully.")
        elif caller == LIVE:
            symbol = gui.configuration.tickerComboBox.currentText()
            interval = helpers.convert_interval(gui.configuration.intervalComboBox.currentText())
            apiSecret = gui.configuration.binanceApiSecret.text()
            apiKey = gui.configuration.binanceApiKey.text()
            if len(apiSecret) == 0:
                raise ValueError('Please specify an API secret key. No API secret key found.')
            elif len(apiKey) == 0:
                raise ValueError("Please specify an API key. No API key found.")
            self.signals.liveActivity.emit(f"Retrieving data for interval {interval}...")
            gui.trader = RealTrader(apiSecret=apiSecret, apiKey=apiKey, interval=interval, symbol=symbol)
            self.signals.liveActivity.emit("Retrieved data successfully.")
        else:
            raise ValueError("Invalid caller.")

        self.initialize_lower_interval_trading(caller=caller, interval=interval)

    def setup_bot(self, caller):
        self.create_trader(caller)
        self.gui.set_parameters(caller)
        self.trader = self.gui.trader if caller == LIVE else self.gui.simulationTrader

        if caller == LIVE:
            if self.gui.configuration.enableTelegramTrading.isChecked():
                self.handle_telegram_bot()
            self.gui.runningLive = True
        elif caller == SIMULATION:
            self.gui.simulationRunningLive = True
        else:
            raise RuntimeError("Invalid type of caller specified.")

    def get_statistics(self):
        trader = self.trader
        net = trader.get_net()
        profit = trader.get_profit()
        stopLoss = trader.get_stop_loss()
        profitLabel = trader.get_profit_or_loss_string(profit=profit)
        percentage = trader.get_profit_percentage(trader.startingBalance, net)
        currentPriceString = f'${trader.dataView.get_current_price()}'
        percentageString = f'{round(percentage, 2)}%'
        profitString = f'${abs(round(profit, 2))}'
        netString = f'${round(net, 2)}'

        optionDetails = []
        for option in trader.tradingOptions:
            optionDetails.append(self.gui.get_option_info(option, trader))

        updateDict = {
            # Statistics window
            'net': net,
            'startingBalanceValue': f'${round(trader.startingBalance, 2)}',
            'currentBalanceValue': f'${round(trader.balance, 2)}',
            'netValue': netString,
            'profitLossLabel': profitLabel,
            'profitLossValue': profitString,
            'percentageValue': percentageString,
            'tradesMadeValue': str(len(trader.trades)),
            'coinOwnedLabel': f'{trader.coinName} Owned',
            'coinOwnedValue': f'{round(trader.coin, 6)}',
            'coinOwedLabel': f'{trader.coinName} Owed',
            'coinOwedValue': f'{round(trader.coinOwed, 6)}',
            'lossPointLabel': trader.get_stop_loss_strategy_string(),
            'lossPointValue': trader.get_safe_rounded_string(stopLoss),
            'customStopPointValue': trader.get_safe_rounded_string(trader.customStopLoss),
            'currentPositionValue': trader.get_position_string(),
            'autonomousValue': str(not trader.inHumanControl),
            'tickerLabel': trader.symbol,
            'tickerValue': currentPriceString,
            'optionDetails': optionDetails
        }

        return updateDict

    def update_data(self, caller):
        """
        Updates data if updated data exists for caller object.
        :param caller: Object type that will be updated.
        """
        gui = self.gui
        if caller == LIVE and not gui.trader.dataView.data_is_updated():
            self.signals.liveActivity.emit('New data found. Updating...')
            gui.trader.dataView.update_data()
            self.signals.liveActivity.emit('Updated data successfully.')
        elif caller == SIMULATION and not gui.simulationTrader.dataView.data_is_updated():
            self.signals.simulationActivity.emit('New data found. Updating...')
            gui.simulationTrader.dataView.update_data()
            self.signals.simulationActivity.emit('Updated data successfully.')

    def trading_loop(self, caller):
        lowerTrend = None
        runningLoop = self.gui.runningLive if caller == LIVE else self.gui.simulationRunningLive

        while runningLoop:
            self.update_data(caller)
            self.gui.handle_logging(caller=caller)
            self.gui.handle_trailing_prices(caller=caller)
            self.gui.handle_trading(caller=caller)
            # crossNotification = self.handle_cross_notification(caller=caller, notification=crossNotification)
            # lowerTrend = self.gui.handle_lower_interval_cross(caller, lowerTrend)
            statDict = self.get_statistics()
            self.signals.updated.emit(caller, statDict)
            runningLoop = self.gui.runningLive if caller == LIVE else self.gui.simulationRunningLive

    @pyqtSlot()
    def run(self):
        """
        Initialise the runner function with passed args, kwargs.
        """
        # Retrieve args/kwargs here; and fire processing using them
        try:
            caller = self.caller
            self.setup_bot(caller=caller)
            self.signals.started.emit(caller)
            self.trading_loop(caller)
        except Exception as e:
            print(f'Error: {e}')
            traceback.print_exc()
            self.signals.error.emit(self.caller, str(e))