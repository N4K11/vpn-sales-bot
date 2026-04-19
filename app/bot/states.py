from aiogram.fsm.state import State, StatesGroup


class TariffCreateState(StatesGroup):
    waiting_payload = State()


class TariffEditState(StatesGroup):
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


class ManualSubscriptionState(StatesGroup):
    waiting_days = State()


class BroadcastState(StatesGroup):
    waiting_text = State()


class PaymentConfigState(StatesGroup):
    waiting_payload = State()


class ServerAgentState(StatesGroup):
    waiting_payload = State()


class ServerBillingState(StatesGroup):
    waiting_payload = State()


class ServerCommandState(StatesGroup):
    waiting_command = State()


class PromoCodeState(StatesGroup):
    waiting_code = State()


class GiftRecipientState(StatesGroup):
    waiting_recipient = State()


class PromoCreateState(StatesGroup):
    waiting_payload = State()


class RenewalDiscountState(StatesGroup):
    waiting_percent = State()