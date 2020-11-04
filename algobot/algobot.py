import assets
import sys
import helpers
import os
import pyqtgraph as pg

from threads import workerThread, backtestThread, botThread
from data import Data
from datetime import datetime
from interface.palettes import *
from backtest import Backtester
from realtrader import RealTrader
from simulationtrader import SimulationTrader
from option import Option
from enums import *
from interface.configuration import Configuration
from interface.otherCommands import OtherCommands
from interface.about import About
from interface.statistics import Statistics

from PyQt5 import uic
from PyQt5.QtWidgets import QMainWindow, QApplication, QMessageBox, QTableWidgetItem
from PyQt5.QtCore import QThreadPool
from PyQt5.QtGui import QIcon
from pyqtgraph import DateAxisItem, mkPen, PlotWidget

app = QApplication(sys.argv)
mainUi = os.path.join('../', 'UI', 'algobot.ui')


class Interface(QMainWindow):
    def __init__(self, parent=None):
        assets.qInitResources()
        super(Interface, self).__init__(parent)  # Initializing object
        uic.loadUi(mainUi, self)  # Loading the main UI
        self.configuration = Configuration()  # Loading configuration
        self.otherCommands = OtherCommands()  # Loading other commands
        self.about = About()  # Loading about information
        self.statistics = Statistics()  # Loading statistics
        self.threadPool = QThreadPool()  # Initiating threading pool
        self.graphs = (
            {'graph': self.simulationGraph, 'plots': []},
            {'graph': self.backtestGraph, 'plots': []},
            {'graph': self.liveGraph, 'plots': []},
            {'graph': self.avgGraph, 'plots': []},
            {'graph': self.simulationAvgGraph, 'plots': []},
        )
        self.setup_graphs()  # Setting up graphs
        self.initiate_slots()  # Initiating slots
        self.threadPool.start(workerThread.Worker(self.load_tickers))  # Load tickers

        self.interfaceDictionary = self.get_interface_dictionary()
        self.advancedLogging = True
        self.runningLive = False
        self.simulationRunningLive = False
        self.backtester: Backtester or None = None
        self.trader: RealTrader or None = None
        self.simulationTrader: SimulationTrader or None = None
        self.simulationLowerIntervalData: Data or None = None
        self.lowerIntervalData: Data or None = None
        self.telegramBot = None
        self.add_to_live_activity_monitor('Initialized interface.')

    def initiate_backtest(self):
        if self.configuration.data is None:
            self.create_popup("No data setup yet for backtesting. Please configure them in settings first.")
            return

        worker = backtestThread.BacktestThread(gui=self)
        worker.signals.started.connect(self.setup_backtester)
        worker.signals.error.connect(self.end_crash_bot_and_create_popup)
        worker.signals.activity.connect(self.update_backtest_gui)
        worker.signals.finished.connect(self.end_backtest)
        self.threadPool.start(worker)

    def update_backtest_gui(self, updatedDict):
        self.backtestProgressBar.setValue(updatedDict['percentage'])
        net = updatedDict['net']
        utc = updatedDict['utc']
        if net < self.backtester.startingBalance:
            self.backtestProfitLabel.setText("Loss")
            self.backtestProfitPercentageLabel.setText("Loss Percentage")
        self.backtestbalance.setText(updatedDict['balance'])
        self.backtestNet.setText(updatedDict['netString'])
        self.backtestCommissionsPaid.setText(updatedDict['commissionsPaid'])
        self.backtestProfit.setText(updatedDict['profit'])
        self.backtestProfitPercentage.setText(updatedDict['profitPercentage'])
        self.backtestTradesMade.setText(updatedDict['tradesMade'])
        self.backtestCurrentPeriod.setText(updatedDict['currentPeriod'])
        self.add_data_to_plot(self.interfaceDictionary[BACKTEST]['mainInterface']['graph'], 0, utc, net)

    def setup_backtester(self, statDict):
        interfaceDict = self.interfaceDictionary[BACKTEST]['mainInterface']
        self.destroy_graph_plots(interfaceDict['graph'])
        self.setup_graph_plots(interfaceDict['graph'], self.backtester, NET_GRAPH)
        for graph in self.graphs:  # Super hacky temporary fix.
            if graph['graph'] == self.backtestGraph:
                initialTimeStamp = self.backtester.data[0]['date_utc'].timestamp()
                finalTimeStamp = self.backtester.data[-1]['date_utc'].timestamp() + 300
                graph['graph'].setLimits(xMin=initialTimeStamp, xMax=finalTimeStamp)
                plot = graph['plots'][0]
                plot['x'] = []
                plot['y'] = []
                plot['plot'].setData(plot['x'], plot['y'])
        self.disable_interface(True, BACKTEST)
        self.backtestStartingBalance.setText(statDict['startingBalance'])
        self.backtestInterval.setText(statDict['interval'])
        self.backtestMarginEnabled.setText(statDict['marginEnabled'])
        self.backtestStopLossPercentage.setText(statDict['stopLossPercentage'])
        self.backtestLossStrategy.setText(statDict['stopLossStrategy'])
        self.backtestStartPeriod.setText(statDict['startPeriod'])
        self.backtestEndPeriod.setText(statDict['endPeriod'])
        # self.backtestMovingAverage1.setText()
        # self.backtestMovingAverage2.setText()
        # self.backtestMovingAverage3.setText()
        # self.backtestMovingAverage4.setText()

    def end_backtest(self, path):
        self.disable_interface(False, BACKTEST)
        self.backtestProgressBar.setValue(100)

        msgBox = QMessageBox()
        msgBox.setIcon(QMessageBox.Information)
        msgBox.setText(f"Backtest results have been saved to {path}.")
        msgBox.setWindowTitle("Backtest Results")
        msgBox.setStandardButtons(QMessageBox.Open | QMessageBox.Close)
        if msgBox.exec_() == QMessageBox.Open:
            os.startfile(path)

    def initiate_bot_thread(self, caller: int):
        """
        Main function that initiates bot thread and handles all data-view logic.
        :param caller: Caller that decides whether a live bot or simulation bot is run.
        """
        self.disable_interface(True, caller, everything=True)
        worker = botThread.BotThread(gui=self, caller=caller)
        worker.signals.error.connect(self.end_crash_bot_and_create_popup)
        worker.signals.activity.connect(self.add_to_monitor)
        worker.signals.started.connect(self.initial_bot_ui_setup)
        worker.signals.updated.connect(self.update_interface_info)
        self.threadPool.start(worker)

    def end_bot_thread(self, caller):
        """
        Ends bot based on caller.
        :param caller: Caller that decides which bot will be ended.
        """
        self.disable_interface(True, caller=caller, everything=True)
        if caller == SIMULATION:
            self.simulationRunningLive = False
            self.simulationTrader.get_simulation_result()
            self.add_to_monitor(caller, "Killed simulation bot.")
            tempTrader = self.simulationTrader
            if self.simulationLowerIntervalData is not None:
                self.simulationLowerIntervalData.dump_to_table()
                self.simulationLowerIntervalData = None
        else:
            self.runningLive = False
            self.telegramBot.stop()
            self.add_to_monitor(caller, 'Killed Telegram bot.')
            self.add_to_monitor(caller, "Killed bot.")
            tempTrader = self.trader
            if self.lowerIntervalData is not None:
                self.lowerIntervalData.dump_to_table()
                self.lowerIntervalData = None

        tempTrader.log_trades()
        self.enable_override(caller, False)
        self.update_trades_table_and_activity_monitor(caller)
        self.disable_interface(False, caller=caller)
        tempTrader.dataView.dump_to_table()
        # self.destroy_trader(caller)

    def end_crash_bot_and_create_popup(self, caller: int, msg: str):
        """
        Function that force ends bot in the event that it crashes.
        """
        self.disable_interface(boolean=False, caller=caller)
        self.add_to_monitor(caller=caller, message=msg)
        self.create_popup(msg)

    def initial_bot_ui_setup(self, caller):
        """
        Sets up UI based on caller.
        :param caller: Caller that determines which UI gets setup.
        """
        trader = self.get_trader(caller)
        interfaceDict = self.interfaceDictionary[caller]['mainInterface']
        self.disable_interface(True, caller, False)
        self.enable_override(caller)
        self.clear_table(interfaceDict['historyTable'])
        self.destroy_graph_plots(interfaceDict['graph'])
        self.destroy_graph_plots(interfaceDict['averageGraph'])
        self.setup_graph_plots(interfaceDict['graph'], trader, NET_GRAPH)
        self.setup_graph_plots(interfaceDict['averageGraph'], trader, AVG_GRAPH)

    def disable_interface(self, boolean, caller, everything=False):
        """
        Function that will control trading configuration interfaces.
        :param everything: Disables everything during initialization.
        :param boolean: If true, configuration settings get disabled.
        :param caller: Caller that determines which configuration settings get disabled.
        """
        boolean = not boolean
        self.interfaceDictionary[caller]['configuration']['mainConfigurationTabWidget'].setEnabled(boolean)
        self.interfaceDictionary[caller]['mainInterface']['runBotButton'].setEnabled(boolean)
        if caller != BACKTEST:
            self.interfaceDictionary[caller]['mainInterface']['customStopLossGroupBox'].setEnabled(not boolean)
        if not everything:
            self.interfaceDictionary[caller]['mainInterface']['endBotButton'].setEnabled(not boolean)
        else:
            self.interfaceDictionary[caller]['mainInterface']['endBotButton'].setEnabled(boolean)

    def update_interface_info(self, caller, statDict):
        """
        Updates interface elements based on caller.
        :param statDict: Dictionary containing statistics.
        :param caller: Object that determines which object gets updated.
        """
        interfaceDict = self.interfaceDictionary[caller]
        self.update_main_interface(interfaceDict, statDict=statDict, caller=caller)
        self.update_trades_table_and_activity_monitor(caller=caller)
        self.handle_position_buttons(caller=caller)
        self.handle_custom_stop_loss_buttons(caller=caller)

    def update_main_interface(self, interfaceDictionary, statDict, caller):
        statisticsDictionary = interfaceDictionary['statistics']
        statisticsDictionary['startingBalanceValue'].setText(statDict['startingBalanceValue'])
        statisticsDictionary['currentBalanceValue'].setText(statDict['currentBalanceValue'])
        statisticsDictionary['netValue'].setText(statDict['netValue'])
        statisticsDictionary['profitLossLabel'].setText(statDict['profitLossLabel'])
        statisticsDictionary['profitLossValue'].setText(statDict['profitLossValue'])
        statisticsDictionary['percentageValue'].setText(statDict['percentageValue'])
        statisticsDictionary['tradesMadeValue'].setText(statDict['tradesMadeValue'])
        statisticsDictionary['coinOwnedLabel'].setText(statDict['coinOwnedLabel'])
        statisticsDictionary['coinOwnedValue'].setText(statDict['coinOwnedValue'])
        statisticsDictionary['coinOwedLabel'].setText(statDict['coinOwedLabel'])
        statisticsDictionary['coinOwedValue'].setText(statDict['coinOwedValue'])
        statisticsDictionary['currentTickerLabel'].setText(statDict['tickerLabel'])
        statisticsDictionary['currentTickerValue'].setText(statDict['tickerValue'])
        statisticsDictionary['lossPointLabel'].setText(statDict['lossPointLabel'])
        statisticsDictionary['lossPointValue'].setText(statDict['lossPointValue'])
        statisticsDictionary['customStopPointValue'].setText(statDict['customStopPointValue'])
        statisticsDictionary['currentPositionValue'].setText(statDict['currentPositionValue'])
        statisticsDictionary['autonomousValue'].setText(statDict['autonomousValue'])

        # These are for main interface window.
        mainInterfaceDictionary = interfaceDictionary['mainInterface']
        mainInterfaceDictionary['profitLabel'].setText(statDict['profitLossLabel'])
        mainInterfaceDictionary['profitValue'].setText(statDict['profitLossValue'])
        mainInterfaceDictionary['percentageValue'].setText(statDict['percentageValue'])
        mainInterfaceDictionary['netTotalValue'].setText(statDict['netValue'])
        mainInterfaceDictionary['tickerLabel'].setText(statDict['tickerLabel'])
        mainInterfaceDictionary['tickerValue'].setText(statDict['tickerValue'])

        net = statDict['net']
        optionDetails = statDict['optionDetails']
        self.update_graphs(net=net, caller=caller, optionDetails=optionDetails)

    def update_graphs(self, net: float, caller, optionDetails: list):
        interfaceDict = self.interfaceDictionary[caller]
        currentUTC = datetime.utcnow().timestamp()
        self.add_data_to_plot(interfaceDict['mainInterface']['graph'], 0, currentUTC, net)

        if len(optionDetails) == 1:
            self.hide_next_moving_averages(caller)

        for index, optionDetail in enumerate(optionDetails):
            initialAverage, finalAverage, initialAverageLabel, finalAverageLabel = optionDetail
            self.add_data_to_plot(interfaceDict['mainInterface']['averageGraph'], index * 2, currentUTC, initialAverage)
            self.add_data_to_plot(interfaceDict['mainInterface']['averageGraph'], index * 2 + 1, currentUTC,
                                  finalAverage)

            if index == 0:
                interfaceDict['statistics']['baseInitialMovingAverageLabel'].setText(initialAverageLabel)
                interfaceDict['statistics']['baseInitialMovingAverageValue'].setText(f'${initialAverage}')
                interfaceDict['statistics']['baseFinalMovingAverageLabel'].setText(finalAverageLabel)
                interfaceDict['statistics']['baseFinalMovingAverageValue'].setText(f'${finalAverage}')
            if index == 1:
                self.show_next_moving_averages(caller=caller)
                interfaceDict['statistics']['nextInitialMovingAverageLabel'].setText(initialAverageLabel)
                interfaceDict['statistics']['nextInitialMovingAverageValue'].setText(f'${initialAverage}')
                interfaceDict['statistics']['nextFinalMovingAverageLabel'].setText(finalAverageLabel)
                interfaceDict['statistics']['nextFinalMovingAverageValue'].setText(f'${finalAverage}')

    def show_next_moving_averages(self, caller):
        """
        :param caller: Caller that will decide which statistics get shown..
        Shows next moving averages statistics based on caller.
        """
        interfaceDict = self.interfaceDictionary[caller]['statistics']
        interfaceDict['nextInitialMovingAverageLabel'].show()
        interfaceDict['nextInitialMovingAverageValue'].show()
        interfaceDict['nextFinalMovingAverageLabel'].show()
        interfaceDict['nextFinalMovingAverageValue'].show()

    def hide_next_moving_averages(self, caller):
        """
        :param caller: Caller that will decide which statistics get hidden.
        Hides next moving averages statistics based on caller.
        """
        interfaceDict = self.interfaceDictionary[caller]['statistics']
        interfaceDict['nextInitialMovingAverageLabel'].hide()
        interfaceDict['nextInitialMovingAverageValue'].hide()
        interfaceDict['nextFinalMovingAverageLabel'].hide()
        interfaceDict['nextFinalMovingAverageValue'].hide()

    def destroy_trader(self, caller):
        """
        Destroys trader based on caller by setting them equal to none.
        :param caller: Caller that determines which trading object gets destroyed.
        """
        if caller == SIMULATION:
            self.simulationTrader = None
        elif caller == LIVE:
            self.trader = None
        elif caller == BACKTEST:
            self.backtester = None
        else:
            raise ValueError("invalid caller type specified.")

    def handle_custom_stop_loss_buttons(self, caller):
        trader = self.get_trader(caller)
        mainDict = self.interfaceDictionary[caller]['mainInterface']

        if trader.customStopLoss is None:
            mainDict['enableCustomStopLossButton'].setEnabled(True)
            mainDict['disableCustomStopLossButton'].setEnabled(False)
        else:
            mainDict['enableCustomStopLossButton'].setEnabled(False)
            mainDict['disableCustomStopLossButton'].setEnabled(True)

    def handle_position_buttons(self, caller):
        """
        Handles interface position buttons based on caller.
        :param caller: Caller object for whose interface buttons will be affected.
        """
        interfaceDict = self.interfaceDictionary[caller]['mainInterface']
        trader = self.get_trader(caller)

        inPosition = False if trader.currentPosition is None else True
        interfaceDict['exitPositionButton'].setEnabled(inPosition)
        interfaceDict['waitOverrideButton'].setEnabled(inPosition)

        if trader.currentPosition == LONG:
            interfaceDict['forceLongButton'].setEnabled(False)
            interfaceDict['forceShortButton'].setEnabled(True)
        elif trader.currentPosition == SHORT:
            interfaceDict['forceLongButton'].setEnabled(True)
            interfaceDict['forceShortButton'].setEnabled(False)
        elif trader.currentPosition is None:
            interfaceDict['forceLongButton'].setEnabled(True)
            interfaceDict['forceShortButton'].setEnabled(True)

    def enable_override(self, caller, enabled=True):
        """
        Enables override interface for which caller specifies.
        :param enabled: Boolean that determines whether override is enabled or disable. By default, it is enabled.
        :param caller: Caller that will specify which interface will have its override interface enabled.
        """
        self.interfaceDictionary[caller]['mainInterface']['overrideGroupBox'].setEnabled(enabled)

    def exit_position(self, caller, humanControl=True):
        """
        Exits position by either giving up control or not. If the boolean humanControl is true, bot gives up control.
        If the boolean is false, the bot still retains control, but exits trade and waits for opposite trend.
        :param humanControl: Boolean that will specify whether bot gives up control or not.
        :param caller: Caller that will specify which trader will exit position.
        """
        trader = self.get_trader(caller)
        interfaceDict = self.interfaceDictionary[caller]['mainInterface']
        if humanControl:
            interfaceDict['pauseBotButton'].setText('Resume Bot')
        else:
            interfaceDict['pauseBotButton'].setText('Pause Bot')
        interfaceDict['forceShortButton'].setEnabled(True)
        interfaceDict['forceLongButton'].setEnabled(True)
        interfaceDict['exitPositionButton'].setEnabled(False)
        interfaceDict['waitOverrideButton'].setEnabled(False)

        trader.inHumanControl = humanControl
        if trader.currentPosition == LONG:
            if humanControl:
                trader.sell_long('Force exited long.', force=True)
            else:
                trader.sell_long('Exited long because of override and resuming autonomous logic.', force=True)
        elif trader.currentPosition == SHORT:
            if humanControl:
                trader.buy_short('Force exited short.', force=True)
            else:
                trader.buy_short('Exited short because of override and resuming autonomous logic.', force=True)

    def force_long(self, caller):
        """
        Forces bot to take long position and gives up its control until bot is resumed.
        :param caller: Caller that will determine with trader will force long.
        """
        trader = self.get_trader(caller)
        self.add_to_monitor(caller, 'Forcing long and stopping autonomous logic.')
        interfaceDict = self.interfaceDictionary[caller]['mainInterface']
        interfaceDict['pauseBotButton'].setText('Resume Bot')
        interfaceDict['forceShortButton'].setEnabled(True)
        interfaceDict['forceLongButton'].setEnabled(False)
        interfaceDict['exitPositionButton'].setEnabled(True)
        interfaceDict['waitOverrideButton'].setEnabled(True)

        trader.inHumanControl = True
        if trader.currentPosition == SHORT:
            trader.buy_short('Exited short because long was forced.', force=True)
        trader.buy_long('Force executed long.', force=True)

    def force_short(self, caller):
        """
        Forces bot to take short position and gives up its control until bot is resumed.
        :param caller: Caller that will determine with trader will force short.
        """
        trader = self.get_trader(caller)
        self.add_to_monitor(caller, 'Forcing short and stopping autonomous logic.')
        interfaceDict = self.interfaceDictionary[caller]['mainInterface']
        interfaceDict['pauseBotButton'].setText('Resume Bot')
        interfaceDict['forceShortButton'].setEnabled(False)
        interfaceDict['forceLongButton'].setEnabled(True)
        interfaceDict['exitPositionButton'].setEnabled(True)
        interfaceDict['waitOverrideButton'].setEnabled(True)

        trader.inHumanControl = True
        if trader.currentPosition == LONG:
            trader.sell_long('Exited long because short was forced.', force=True)
        trader.sell_short('Force executed short.', force=True)

    def pause_or_resume_bot(self, caller):
        """
        Pauses or resumes bot logic based on caller.
        :param caller: Caller object that specifies which trading object will be paused or resumed.
        """
        trader = self.get_trader(caller)
        pauseButton = self.interfaceDictionary[caller]['mainInterface']['pauseBotButton']
        if pauseButton.text() == 'Pause Bot':
            trader.inHumanControl = True
            pauseButton.setText('Resume Bot')
            self.add_to_monitor(caller, 'Pausing bot logic.')
        else:
            trader.inHumanControl = False
            pauseButton.setText('Pause Bot')
            self.add_to_monitor(caller, 'Resuming bot logic.')

    def set_advanced_logging(self, boolean):
        """
        Sets logging standard.
        :param boolean: Boolean that will determine whether logging is advanced or not. If true, advanced, else regular.
        """
        if self.advancedLogging:
            self.add_to_live_activity_monitor(f'Logging method has been changed to advanced.')
        else:
            self.add_to_live_activity_monitor(f'Logging method has been changed to simple.')
        self.advancedLogging = boolean

    def set_parameters(self, caller):
        """
        Retrieves moving average options and loss settings based on caller.
        :param caller: Caller that dictates which parameters get set.
        :return:
        """
        trader = self.get_trader(caller)
        trader.lossStrategy, trader.lossPercentageDecimal = self.get_loss_settings(caller)
        trader.tradingOptions = self.get_trading_options(caller)

    def set_custom_stop_loss(self, caller, enable=True):
        """
        Enables or disables custom stop loss.
        :param enable: Boolean that determines whether custom stop loss is enabled or disabled. Default is enable.
        :param caller: Caller that decides which trader object gets the stop loss.
        """
        trader = self.get_trader(caller)
        mainDict = self.interfaceDictionary[caller]['mainInterface']
        if enable:
            trader.customStopLoss = mainDict['customStopLossValue'].value()
            mainDict['enableCustomStopLossButton'].setEnabled(False)
            mainDict['disableCustomStopLossButton'].setEnabled(True)
            self.add_to_monitor(caller, f'Set custom stop loss at ${trader.customStopLoss}')
        else:
            trader.customStopLoss = None
            mainDict['enableCustomStopLossButton'].setEnabled(True)
            mainDict['disableCustomStopLossButton'].setEnabled(False)
            self.add_to_monitor(caller, f'Removed custom stop loss.')

    def get_trading_options(self, caller) -> list:
        """
        Returns trading options based on caller specified.
        :param caller: Caller object that will determine which trading options are returned.
        :return: Trading options based on caller.
        """
        configDictionary = self.interfaceDictionary[caller]['configuration']
        baseAverageType = configDictionary['baseAverageType'].currentText()
        baseParameter = configDictionary['baseParameter'].currentText().lower()
        baseInitialValue = configDictionary['baseInitialValue'].value()
        baseFinalValue = configDictionary['baseFinalValue'].value()
        options = [Option(baseAverageType, baseParameter, baseInitialValue, baseFinalValue)]

        if configDictionary['doubleCrossCheck'].isChecked():
            additionalAverageType = configDictionary['additionalAverageType'].currentText()
            additionalParameter = configDictionary['additionalParameter'].currentText().lower()
            additionalInitialValue = configDictionary['additionalInitialValue'].value()
            additionalFinalValue = configDictionary['additionalFinalValue'].value()
            option = Option(additionalAverageType, additionalParameter, additionalInitialValue, additionalFinalValue)
            options.append(option)

        return options

    def get_loss_settings(self, caller) -> tuple:
        """
        Returns loss settings for caller specified.
        :param caller: Caller for which loss settings will be returned.
        :return: Tuple with stop loss type and loss percentage.
        """
        configDictionary = self.interfaceDictionary[caller]['configuration']
        if configDictionary['trailingLossRadio'].isChecked():
            return TRAILING_LOSS, configDictionary['lossPercentage'].value() / 100
        return STOP_LOSS, configDictionary['lossPercentage'].value() / 100

    @staticmethod
    def get_option_info(option: Option, trader) -> tuple:
        """
        Returns basic information about option provided.
        :param option: Option object for whose information will be retrieved.
        :param trader: Trader object to be used to get averages.
        :return: Tuple of initial average, final average, initial option name, and final option name.
        """
        initialAverage = trader.get_average(option.movingAverage, option.parameter, option.initialBound)
        finalAverage = trader.get_average(option.movingAverage, option.parameter, option.finalBound)
        initialName = f'{option.movingAverage}({option.initialBound}) {option.parameter.capitalize()}'
        finalName = f'{option.movingAverage}({option.finalBound}) {option.parameter.capitalize()}'
        return initialAverage, finalAverage, initialName, finalName

    def setup_graphs(self):
        """
        Sets up all available graphs in application.
        """
        currentDate = datetime.utcnow().timestamp()
        nextDate = currentDate + 3600000

        for graph in self.graphs:
            graph = graph['graph']
            graph.setAxisItems({'bottom': DateAxisItem()})
            graph.setBackground('w')
            graph.setLabel('left', 'USDT')
            graph.setLabel('bottom', 'Datetime in UTC')
            graph.addLegend()
            if graph != self.backtestGraph:
                graph.setLimits(xMin=currentDate, xMax=nextDate)

            if graph == self.backtestGraph:
                graph.setTitle("Backtest Price Change")
            elif graph == self.simulationGraph:
                graph.setTitle("Simulation Price Change")
            elif graph == self.liveGraph:
                graph.setTitle("Live Price Change")
            elif graph == self.simulationAvgGraph:
                graph.setTitle("Simulation Moving Averages")
            elif graph == self.avgGraph:
                graph.setTitle("Live Moving Averages")

        # self.graphWidget.setLimits(xMin=currentDate, xMax=nextDate)
        # self.graphWidget.plotItem.setMouseEnabled(y=False)

    def add_data_to_plot(self, targetGraph: PlotWidget, plotIndex: int, x: float, y: float):
        """
        Adds data to plot in provided graph.
        :param targetGraph: Graph to use for plot to add data to.
        :param plotIndex: Index of plot in target graph's list of plots.
        :param x: X value or values to add depending on whether arg is a list or a function.
        :param y: Y value or values to add depending on whether arg is a list or a function.
        """
        for graph in self.graphs:
            if graph['graph'] == targetGraph:
                plot = graph['plots'][plotIndex]
                plot['x'].append(x)
                plot['y'].append(y)
                plot['plot'].setData(plot['x'], plot['y'])

    def append_plot_to_graph(self, targetGraph, toAdd: list):
        """
        Appends plot to graph provided.
        :param targetGraph: Graph to add plot to.
        :param toAdd: List of plots to add to target graph.
        """
        for graph in self.graphs:
            if graph['graph'] == targetGraph:
                graph['plots'] += toAdd

    def destroy_graph_plots(self, targetGraph: PlotWidget):
        """
        Resets graph plots for graph provided. Does not do anything. Fixing needed.
        :param targetGraph: Graph to destroy plots for.
        """
        for graph in self.graphs:
            if graph['graph'] == targetGraph:
                graph['graph'].clear()
                graph['plots'] = []

    def setup_net_graph_plot(self, graph: PlotWidget, trader, color: str):
        """
        Sets up net balance plot for graph provided.
        :param trader: Type of trader that will use this graph.
        :param graph: Graph where plot will be setup.
        :param color: Color plot will be setup in.
        """
        net = trader.startingBalance
        currentDateTimestamp = datetime.utcnow().timestamp()
        if graph != self.backtestGraph:
            graph.setLimits(xMin=currentDateTimestamp)

        self.append_plot_to_graph(graph, [{
            'plot': self.create_graph_plot(graph, (currentDateTimestamp,), (net,),
                                           color=color, plotName='Net'),
            'x': [currentDateTimestamp],
            'y': [net]
        }])

    def setup_average_graph_plots(self, graph: PlotWidget, trader, colors: list):
        """
        Sets up moving average plots for graph provided.
        :param trader: Type of trader that will use this graph.
        :param graph: Graph where plots will be setup.
        :param colors: List of colors plots will be setup in.
        """
        currentDateTimestamp = datetime.utcnow().timestamp()
        graph.setLimits(xMin=currentDateTimestamp)
        colorCounter = 1
        for option in trader.tradingOptions:
            initialAverage, finalAverage, initialName, finalName = self.get_option_info(option, trader)
            initialPlotDict = {
                'plot': self.create_graph_plot(graph, (currentDateTimestamp,), (initialAverage,),
                                               color=colors[colorCounter], plotName=initialName),
                'x': [currentDateTimestamp],
                'y': [initialAverage]
            }
            secondaryPlotDict = {
                'plot': self.create_graph_plot(graph, (currentDateTimestamp,), (finalAverage,),
                                               color=colors[colorCounter + 1], plotName=finalName),
                'x': [currentDateTimestamp],
                'y': [finalAverage]
            }
            colorCounter += 2
            self.append_plot_to_graph(graph, [initialPlotDict, secondaryPlotDict])

    def setup_graph_plots(self, graph, trader, graphType):
        """
        Setups graph plots for graph, trade, and graphType specified.
        :param graph: Graph that will be setup.
        :param trader: Trade object that will use this graph.
        :param graphType: Graph type; i.e. moving average or net balance.
        """
        colors = self.get_graph_colors()
        if graphType == NET_GRAPH:
            self.setup_net_graph_plot(graph=graph, trader=trader, color=colors[0])
        elif graphType == AVG_GRAPH:
            self.setup_average_graph_plots(graph=graph, trader=trader, colors=colors)
        else:
            raise TypeError("Invalid type of graph provided.")

    def get_graph_colors(self):
        """
        Returns graph colors to be placed based on configuration.
        """
        config = self.configuration
        colorDict = {'blue': 'b',
                     'green': 'g',
                     'red': 'r',
                     'cyan': 'c',
                     'magenta': 'm',
                     'yellow': 'y',
                     'black': 'k',
                     'white': 'w'}
        colors = [config.balanceColor.currentText(), config.avg1Color.currentText(), config.avg2Color.currentText(),
                  config.avg3Color.currentText(), config.avg4Color.currentText()]
        return [colorDict[color.lower()] for color in colors]

    @staticmethod
    def create_graph_plot(graph, x, y, plotName, color):
        """
        Creates a graph plot with parameters provided.
        :param graph: Graph function will plot on.
        :param x: X values of graph.
        :param y: Y values of graph.
        :param plotName: Name of graph.
        :param color: Color graph will be drawn in.
        """
        pen = mkPen(color=color)
        return graph.plot(x, y, name=plotName, pen=pen)

    @staticmethod
    def clear_table(table):
        """
        Sets table row count to 0.
        :param table: Table which is to be cleared.
        """
        table.clearContents()
        table.setRowCount(0)

    @staticmethod
    def test_table(table, trade):
        """
        Initial function made to test table functionality in QT.
        :param table: Table to insert row at.
        :param trade: Trade information to add.
        """
        rowPosition = table.rowCount()
        columns = table.columnCount()

        table.insertRow(rowPosition)
        for column in range(columns):
            table.setItem(rowPosition, column, QTableWidgetItem(str(trade[column])))

    def add_to_monitor(self, caller, message):
        if caller == SIMULATION:
            self.add_to_simulation_activity_monitor(message)
        elif caller == LIVE:
            self.add_to_live_activity_monitor(message)
        elif caller == BACKTEST:
            self.add_to_backtest_monitor(message)
        else:
            raise TypeError("Invalid type of caller specified.")

    def add_to_backtest_monitor(self, message: str):
        self.add_to_table(self.backtestTable, [message])

    def add_to_simulation_activity_monitor(self, message: str):
        """
        Function that adds activity information to the simulation activity monitor.
        :param message: Message to add to simulation activity log.
        """
        self.add_to_table(self.simulationActivityMonitor, [message])

    def add_to_live_activity_monitor(self, message: str):
        """
        Function that adds activity information to activity monitor.
        :param message: Message to add to activity log.
        """
        self.add_to_table(self.activityMonitor, [message])

    @staticmethod
    def add_to_table(table, data):
        """
        Function that will add specified data to a provided table.
        :param table: Table we will add data to.
        :param data: Data we will add to table.
        """
        rowPosition = table.rowCount()
        columns = table.columnCount()

        data.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        if len(data) != columns:
            raise ValueError('Data needs to have the same amount of columns as table.')

        table.insertRow(rowPosition)
        for column in range(0, columns):
            table.setItem(rowPosition, column, QTableWidgetItem(str(data[column])))

    def update_trades_table_and_activity_monitor(self, caller):
        """
        Updates trade table and activity based on caller.
        :param caller: Caller object that will rule which tables get updated.
        """
        table = self.interfaceDictionary[caller]['mainInterface']['historyTable']
        trades = self.get_trader(caller).trades

        if len(trades) > table.rowCount():  # Basically, only update when row count is not equal to trades count.
            remaining = len(trades) - table.rowCount()
            for trade in trades[-remaining:]:
                tradeData = [trade['orderID'],
                             trade['pair'],
                             trade['price'],
                             trade['percentage'],
                             trade['profit'],
                             trade['method'],
                             trade['action']]
                self.add_to_table(table, tradeData)
                self.add_to_monitor(caller, trade['action'])

    def closeEvent(self, event):
        """
        Close event override. Makes user confirm they want to end program if something is running live.
        :param event: close event
        """
        qm = QMessageBox
        message = ""
        if self.simulationRunningLive and self.runningLive:
            message = "There is a live bot and a simulation running."
        elif self.simulationRunningLive:
            message = "There is a simulation running."
        elif self.runningLive:
            message = "There is live bot running."
        ret = qm.question(self, 'Close?', f"{message} Are you sure you want to end Algobot?",
                          qm.Yes | qm.No)

        if ret == qm.Yes:
            if self.runningLive:
                self.end_bot_thread(LIVE)
            elif self.simulationRunningLive:
                self.end_bot_thread(SIMULATION)
            event.accept()
        else:
            event.ignore()

    def show_main_settings(self):
        """
        Opens main settings in the configuration window.
        """
        self.configuration.show()
        self.configuration.configurationTabWidget.setCurrentIndex(0)
        self.configuration.mainConfigurationTabWidget.setCurrentIndex(0)

    def show_backtest_settings(self):
        """
        Opens backtest settings in the configuration window.
        """
        self.configuration.show()
        self.configuration.configurationTabWidget.setCurrentIndex(1)
        self.configuration.backtestConfigurationTabWidget.setCurrentIndex(0)

    def show_simulation_settings(self):
        """
        Opens simulation settings in the configuration window.
        """
        self.configuration.show()
        self.configuration.configurationTabWidget.setCurrentIndex(2)
        self.configuration.simulationConfigurationTabWidget.setCurrentIndex(0)

    def create_configuration_slots(self):
        """
        Creates configuration slots.
        """
        self.configuration.lightModeRadioButton.toggled.connect(lambda: self.set_light_mode())
        self.configuration.darkModeRadioButton.toggled.connect(lambda: self.set_dark_mode())
        self.configuration.bloombergModeRadioButton.toggled.connect(lambda: self.set_bloomberg_mode())
        self.configuration.bullModeRadioButton.toggled.connect(lambda: self.set_bull_mode())
        self.configuration.bearModeRadioButton.toggled.connect(lambda: self.set_bear_mode())
        self.configuration.printingModeRadioButton.toggled.connect(lambda: self.set_printing_mode())
        self.configuration.simpleLoggingRadioButton.clicked.connect(lambda: self.set_advanced_logging(False))
        self.configuration.advancedLoggingRadioButton.clicked.connect(lambda: self.set_advanced_logging(True))

    def create_action_slots(self):
        """
        Creates actions slots.
        """
        self.otherCommandsAction.triggered.connect(lambda: self.otherCommands.show())
        self.configurationAction.triggered.connect(lambda: self.configuration.show())
        self.statisticsAction.triggered.connect(lambda: self.statistics.show())
        self.aboutAlgobotAction.triggered.connect(lambda: self.about.show())

    def create_bot_slots(self):
        """
        Creates bot slots.
        """
        self.runBotButton.clicked.connect(lambda: self.initiate_bot_thread(caller=LIVE))
        self.endBotButton.clicked.connect(lambda: self.end_bot_thread(caller=LIVE))
        self.configureBotButton.clicked.connect(self.show_main_settings)
        self.forceLongButton.clicked.connect(lambda: self.force_long(LIVE))
        self.forceShortButton.clicked.connect(lambda: self.force_short(LIVE))
        self.pauseBotButton.clicked.connect(lambda: self.pause_or_resume_bot(LIVE))
        self.exitPositionButton.clicked.connect(lambda: self.exit_position(LIVE, True))
        self.waitOverrideButton.clicked.connect(lambda: self.exit_position(LIVE, False))
        self.enableCustomStopLossButton.clicked.connect(lambda: self.set_custom_stop_loss(LIVE, True))
        self.disableCustomStopLossButton.clicked.connect(lambda: self.set_custom_stop_loss(LIVE, False))

    def create_simulation_slots(self):
        """
        Creates simulation slots.
        """
        self.runSimulationButton.clicked.connect(lambda: self.initiate_bot_thread(caller=SIMULATION))
        self.endSimulationButton.clicked.connect(lambda: self.end_bot_thread(caller=SIMULATION))
        self.configureSimulationButton.clicked.connect(self.show_simulation_settings)
        self.forceLongSimulationButton.clicked.connect(lambda: self.force_long(SIMULATION))
        self.forceShortSimulationButton.clicked.connect(lambda: self.force_short(SIMULATION))
        self.pauseBotSimulationButton.clicked.connect(lambda: self.pause_or_resume_bot(SIMULATION))
        self.exitPositionSimulationButton.clicked.connect(lambda: self.exit_position(SIMULATION, True))
        self.waitOverrideSimulationButton.clicked.connect(lambda: self.exit_position(SIMULATION, True))
        self.enableSimulationCustomStopLossButton.clicked.connect(lambda: self.set_custom_stop_loss(SIMULATION, True))
        self.disableSimulationCustomStopLossButton.clicked.connect(lambda: self.set_custom_stop_loss(SIMULATION, False))

    def create_backtest_slots(self):
        """
        Creates backtest slots.
        """
        self.configureBacktestButton.clicked.connect(self.show_backtest_settings)
        self.runBacktestButton.clicked.connect(self.initiate_backtest)
        self.endBacktestButton.clicked.connect(self.end_backtest)

    def create_interface_slots(self):
        """
        Creates interface slots.
        """
        self.create_bot_slots()
        self.create_simulation_slots()
        self.create_backtest_slots()

    def initiate_slots(self):
        """
        Initiates all interface slots.
        """
        self.create_action_slots()
        self.create_configuration_slots()
        self.create_interface_slots()

    def load_tickers(self):
        """
        Loads all available tickers from Binance API and displays them on appropriate combo boxes in application.
        """
        tickers = [ticker['symbol'] for ticker in Data(loadData=False).binanceClient.get_all_tickers()
                   if 'USDT' in ticker['symbol']]
        tickers.sort()
        self.configuration.tickerComboBox.clear()  # Clear all existing live tickers.
        self.configuration.backtestTickerComboBox.clear()  # Clear all existing backtest tickers.
        self.configuration.simulationTickerComboBox.clear()  # Clear all existing simulation tickers.

        self.configuration.tickerComboBox.addItems(tickers)  # Add the tickers to list of live tickers.
        self.configuration.backtestTickerComboBox.addItems(tickers)  # Add the tickers to list of backtest tickers.
        self.configuration.simulationTickerComboBox.addItems(tickers)  # Add the tickers to list of simulation tickers.

        self.otherCommands.csvGenerationTicker.clear()  # Clear CSV generation tickers.
        self.otherCommands.csvGenerationTicker.addItems(tickers)  # Add the tickers to list of CSV generation tickers.

    def create_popup(self, msg):
        """
        Creates a popup with message provided.
        :param msg: Message provided.
        """
        if '-1021' in msg:
            msg = msg + ' Please sync your system time.'
        if 'list index out of range' in msg:
            pair = self.configuration.tickerComboBox.currentText()
            msg = f'You may not have any assets in the symbol {pair}. Please check Binance and try again.'
        QMessageBox.about(self, 'Warning', msg)

    def set_dark_mode(self):
        """
        Switches interface to a dark theme.
        """
        app.setPalette(get_dark_palette())
        for graph in self.graphs:
            graph = graph['graph']
            graph.setBackground('k')

    def set_light_mode(self):
        """
        Switches interface to a light theme.
        """
        app.setPalette(get_light_palette())
        for graph in self.graphs:
            graph = graph['graph']
            graph.setBackground('w')

    def set_bloomberg_mode(self):
        """
        Switches interface to bloomberg theme.
        """
        app.setPalette(get_bloomberg_palette())
        for graph in self.graphs:
            graph = graph['graph']
            graph.setBackground('k')

    def set_bear_mode(self):
        """
        Sets bear mode color theme. Theme is red and black mimicking a red day.
        """
        app.setPalette(get_red_palette())
        for graph in self.graphs:
            graph = graph['graph']
            graph.setBackground('k')

    def set_bull_mode(self):
        """
        Sets bull mode color theme. Theme is green and black mimicking a green day.
        """
        app.setPalette(get_green_palette())
        for graph in self.graphs:
            graph = graph['graph']
            graph.setBackground('k')

    def set_printing_mode(self):
        """
        Sets printing mode color theme. Theme is dark green and white mimicking dollars.
        """
        app.setPalette(get_light_green_palette())
        for graph in self.graphs:
            graph = graph['graph']
            graph.setBackground('w')

    def get_lower_interval_data(self, caller) -> Data:
        """
        Returns a lower interval data object.
        :param caller: Caller that determines which lower interval data object gets returned.
        :return: Data object.
        """
        if caller == SIMULATION:
            return self.simulationLowerIntervalData
        elif caller == LIVE:
            return self.lowerIntervalData
        else:
            raise TypeError("Invalid type of caller specified.")

    def get_trader(self, caller) -> SimulationTrader:
        """
        Returns a trader object.
        :param caller: Caller that decides which trader object gets returned.
        :return: Trader object.
        """
        if caller == SIMULATION:
            return self.simulationTrader
        elif caller == LIVE:
            return self.trader
        elif caller == BACKTEST:
            return self.backtester
        else:
            raise TypeError("Invalid type of caller specified.")

    # noinspection DuplicatedCode
    def get_interface_dictionary(self, caller=None):
        """
        Returns dictionary of objects from QT. Used for DRY principles.
        :param caller: Caller that will determine which sub dictionary gets returned.
        :return: Dictionary of objects.
        """
        interfaceDictionary = {
            SIMULATION: {
                'statistics': {
                    'startingBalanceValue': self.statistics.simulationStartingBalanceValue,
                    'currentBalanceValue': self.statistics.simulationCurrentBalanceValue,
                    'netValue': self.statistics.simulationNetValue,
                    'profitLossLabel': self.statistics.simulationProfitLossLabel,
                    'profitLossValue': self.statistics.simulationProfitLossValue,
                    'percentageValue': self.statistics.simulationPercentageValue,
                    'tradesMadeValue': self.statistics.simulationTradesMadeValue,
                    'coinOwnedLabel': self.statistics.simulationCoinOwnedLabel,
                    'coinOwnedValue': self.statistics.simulationCoinOwnedValue,
                    'coinOwedLabel': self.statistics.simulationCoinOwedLabel,
                    'coinOwedValue': self.statistics.simulationCoinOwedValue,
                    'currentTickerLabel': self.statistics.simulationCurrentTickerLabel,
                    'currentTickerValue': self.statistics.simulationCurrentTickerValue,
                    'lossPointLabel': self.statistics.simulationLossPointLabel,
                    'lossPointValue': self.statistics.simulationLossPointValue,
                    'customStopPointValue': self.statistics.simulationCustomStopPointValue,
                    'currentPositionValue': self.statistics.simulationCurrentPositionValue,
                    'autonomousValue': self.statistics.simulationAutonomousValue,
                    'baseInitialMovingAverageLabel': self.statistics.simulationBaseInitialMovingAverageLabel,
                    'baseInitialMovingAverageValue': self.statistics.simulationBaseInitialMovingAverageValue,
                    'baseFinalMovingAverageLabel': self.statistics.simulationBaseFinalMovingAverageLabel,
                    'baseFinalMovingAverageValue': self.statistics.simulationBaseFinalMovingAverageValue,
                    'nextInitialMovingAverageLabel': self.statistics.simulationNextInitialMovingAverageLabel,
                    'nextInitialMovingAverageValue': self.statistics.simulationNextInitialMovingAverageValue,
                    'nextFinalMovingAverageLabel': self.statistics.simulationNextFinalMovingAverageLabel,
                    'nextFinalMovingAverageValue': self.statistics.simulationNextFinalMovingAverageValue
                },
                'mainInterface': {
                    # Portfolio
                    'profitLabel': self.simulationProfitLabel,
                    'profitValue': self.simulationProfitValue,
                    'percentageValue': self.simulationPercentageValue,
                    'netTotalValue': self.simulationNetTotalValue,
                    'tickerLabel': self.simulationTickerLabel,
                    'tickerValue': self.simulationTickerValue,
                    'customStopLossValue': self.customSimulationStopLossValue,
                    # Buttons
                    'pauseBotButton': self.pauseBotSimulationButton,
                    'runBotButton': self.runSimulationButton,
                    'endBotButton': self.endSimulationButton,
                    'forceShortButton': self.forceShortSimulationButton,
                    'forceLongButton': self.forceLongSimulationButton,
                    'exitPositionButton': self.exitPositionSimulationButton,
                    'waitOverrideButton': self.waitOverrideSimulationButton,
                    'enableCustomStopLossButton': self.enableSimulationCustomStopLossButton,
                    'disableCustomStopLossButton': self.disableSimulationCustomStopLossButton,
                    # Groupboxes
                    'overrideGroupBox': self.simulationOverrideGroupBox,
                    'customStopLossGroupBox': self.customSimulationStopLossGroupBox,
                    # Graphs
                    'graph': self.simulationGraph,
                    'averageGraph': self.simulationAvgGraph,
                    # Table
                    'historyTable': self.simulationHistoryTable,
                },
                'configuration': {
                    'baseAverageType': self.configuration.simulationAverageTypeComboBox,
                    'baseParameter': self.configuration.simulationParameterComboBox,
                    'baseInitialValue': self.configuration.simulationInitialValueSpinBox,
                    'baseFinalValue': self.configuration.simulationFinalValueSpinBox,
                    'doubleCrossCheck': self.configuration.simulationDoubleCrossCheckMark,
                    'additionalAverageType': self.configuration.simulationDoubleAverageComboBox,
                    'additionalParameter': self.configuration.simulationDoubleParameterComboBox,
                    'additionalInitialValue': self.configuration.simulationDoubleInitialValueSpinBox,
                    'additionalFinalValue': self.configuration.simulationDoubleFinalValueSpinBox,
                    'trailingLossRadio': self.configuration.simulationTrailingLossRadio,
                    'lossPercentage': self.configuration.simulationLossPercentageSpinBox,
                    'mainConfigurationTabWidget': self.configuration.simulationConfigurationTabWidget,
                    'ticker': self.configuration.simulationTickerComboBox,
                    'interval': self.configuration.simulationIntervalComboBox,
                    'lowerIntervalCheck': self.configuration.lowerIntervalSimulationCheck,
                }
            },
            LIVE: {
                'statistics': {
                    'startingBalanceValue': self.statistics.startingBalanceValue,
                    'currentBalanceValue': self.statistics.currentBalanceValue,
                    'netValue': self.statistics.netValue,
                    'profitLossLabel': self.statistics.profitLossLabel,
                    'profitLossValue': self.statistics.profitLossValue,
                    'percentageValue': self.statistics.percentageValue,
                    'tradesMadeValue': self.statistics.tradesMadeValue,
                    'coinOwnedLabel': self.statistics.coinOwnedLabel,
                    'coinOwnedValue': self.statistics.coinOwnedValue,
                    'coinOwedLabel': self.statistics.coinOwedLabel,
                    'coinOwedValue': self.statistics.coinOwedValue,
                    'currentTickerLabel': self.statistics.currentTickerLabel,
                    'currentTickerValue': self.statistics.currentTickerValue,
                    'lossPointLabel': self.statistics.lossPointLabel,
                    'lossPointValue': self.statistics.lossPointValue,
                    'customStopPointValue': self.statistics.customStopPointValue,
                    'currentPositionValue': self.statistics.currentPositionValue,
                    'autonomousValue': self.statistics.autonomousValue,
                    'baseInitialMovingAverageLabel': self.statistics.baseInitialMovingAverageLabel,
                    'baseInitialMovingAverageValue': self.statistics.baseInitialMovingAverageValue,
                    'baseFinalMovingAverageLabel': self.statistics.baseFinalMovingAverageLabel,
                    'baseFinalMovingAverageValue': self.statistics.baseFinalMovingAverageValue,
                    'nextInitialMovingAverageLabel': self.statistics.nextInitialMovingAverageLabel,
                    'nextInitialMovingAverageValue': self.statistics.nextInitialMovingAverageValue,
                    'nextFinalMovingAverageLabel': self.statistics.nextFinalMovingAverageLabel,
                    'nextFinalMovingAverageValue': self.statistics.nextFinalMovingAverageValue
                },
                'mainInterface': {
                    # Portfolio
                    'profitLabel': self.profitLabel,
                    'profitValue': self.profitValue,
                    'percentageValue': self.percentageValue,
                    'netTotalValue': self.netTotalValue,
                    'tickerLabel': self.tickerLabel,
                    'tickerValue': self.tickerValue,
                    'customStopLossValue': self.customStopLossValue,
                    # Buttons
                    'pauseBotButton': self.pauseBotButton,
                    'runBotButton': self.runBotButton,
                    'endBotButton': self.endBotButton,
                    'forceShortButton': self.forceShortButton,
                    'forceLongButton': self.forceLongButton,
                    'exitPositionButton': self.exitPositionButton,
                    'waitOverrideButton': self.waitOverrideButton,
                    'enableCustomStopLossButton': self.enableCustomStopLossButton,
                    'disableCustomStopLossButton': self.disableCustomStopLossButton,
                    # Groupboxes
                    'overrideGroupBox': self.overrideGroupBox,
                    'customStopLossGroupBox': self.customStopLossGroupBox,
                    # Graphs
                    'graph': self.liveGraph,
                    'averageGraph': self.avgGraph,
                    # Table
                    'historyTable': self.historyTable,
                },
                'configuration': {
                    'baseAverageType': self.configuration.averageTypeComboBox,
                    'baseParameter': self.configuration.parameterComboBox,
                    'baseInitialValue': self.configuration.initialValueSpinBox,
                    'baseFinalValue': self.configuration.finalValueSpinBox,
                    'doubleCrossCheck': self.configuration.doubleCrossCheckMark,
                    'additionalAverageType': self.configuration.doubleAverageComboBox,
                    'additionalParameter': self.configuration.doubleParameterComboBox,
                    'additionalInitialValue': self.configuration.doubleInitialValueSpinBox,
                    'additionalFinalValue': self.configuration.doubleFinalValueSpinBox,
                    'trailingLossRadio': self.configuration.trailingLossRadio,
                    'lossPercentage': self.configuration.lossPercentageSpinBox,
                    'mainConfigurationTabWidget': self.configuration.mainConfigurationTabWidget,
                    'ticker': self.configuration.tickerComboBox,
                    'interval': self.configuration.intervalComboBox,
                    'lowerIntervalCheck': self.configuration.lowerIntervalCheck,
                }
            },
            BACKTEST: {
                'configuration': {
                    'baseAverageType': self.configuration.backtestAverageTypeComboBox,
                    'baseParameter': self.configuration.backtestParameterComboBox,
                    'baseInitialValue': self.configuration.backtestInitialValueSpinBox,
                    'baseFinalValue': self.configuration.backtestFinalValueSpinBox,
                    'doubleCrossCheck': self.configuration.backtestDoubleCrossCheckMark,
                    'additionalAverageType': self.configuration.backtestDoubleAverageComboBox,
                    'additionalParameter': self.configuration.backtestDoubleParameterComboBox,
                    'additionalInitialValue': self.configuration.backtestDoubleInitialValueSpinBox,
                    'additionalFinalValue': self.configuration.backtestDoubleFinalValueSpinBox,
                    'trailingLossRadio': self.configuration.backtestTrailingLossRadio,
                    'lossPercentage': self.configuration.backtestLossPercentageSpinBox,
                    'mainConfigurationTabWidget': self.configuration.backtestConfigurationTabWidget
                },
                'mainInterface': {
                    'runBotButton': self.runBacktestButton,
                    'endBotButton': self.endBacktestButton,
                    # Graphs
                    'graph': self.backtestGraph,
                    # Table
                    'historyTable': self.backtestTable,
                }
            }
        }
        if caller is not None:
            return interfaceDictionary[caller]
        return interfaceDictionary


def main():
    app.setStyle('Fusion')
    helpers.initialize_logger()
    interface = Interface()
    interface.showMaximized()
    app.setWindowIcon(QIcon('../media/algobotwolf.png'))
    sys.excepthook = except_hook
    sys.exit(app.exec_())


def except_hook(cls, exception, trace_back):
    sys.__excepthook__(cls, exception, trace_back)


if __name__ == '__main__':
    main()
