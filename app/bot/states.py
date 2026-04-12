from aiogram.fsm.state import State, StatesGroup


class TariffCreateState(StatesGroup):
    waiting_payload = State()


class ServerCreateState(StatesGroup):
    waiting_payload = State()


class TextEditState(StatesGroup):
    waiting_body = State()


class ReferralEditState(StatesGroup):
    waiting_percent = State()


class TrialEditState(StatesGroup):
    waiting_days = State()


class BalanceGrantState(StatesGroup):
    waiting_amount = State()


class BroadcastState(StatesGroup):
    waiting_text = State()


class ManualSubscriptionState(StatesGroup):
    waiting_days = State()


class TariffEditState(StatesGroup):
    waiting_payload = State()


class PaymentConfigState(StatesGroup):
    waiting_payload = State()


class ServerAgentState(StatesGroup):
    waiting_payload = State()


class ServerBillingState(StatesGroup):
    waiting_payload = State()


class ServerCommandState(StatesGroup):
    waiting_command = State()
