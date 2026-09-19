"""Pure State Pattern for the restricted repair lifecycle.

States own their outgoing transitions and guards. Persistence, authorization and
side effects belong to the order service, keeping this module easy to exercise.
"""

from dataclasses import dataclass

from app.models.enums import OrderStatus


class InvalidTransition(ValueError):
    """A lifecycle rule prevents the requested transition."""


@dataclass(frozen=True)
class StateContext:
    has_device_identity: bool = False
    has_physical_checklist: bool = False
    has_technician: bool = False
    has_technical_report: bool = False
    quote_is_approved: bool = False
    stock_available: bool = False
    fully_paid: bool = False
    approval_decision: bool | None = None


class OrderState:
    status: OrderStatus
    targets: frozenset[OrderStatus] = frozenset()

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        if target not in self.targets:
            raise InvalidTransition(
                f"Transição de {self.status.value} para {target.value} não permitida."
            )

    def transition(self, target: OrderStatus, context: StateContext) -> "OrderState":
        self.validate(target, context)
        return STATES[target]


class IntakeState(OrderState):
    status = OrderStatus.INTAKE
    targets = frozenset({OrderStatus.ANALYZING})

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        super().validate(target, context)
        if not context.has_device_identity or not context.has_physical_checklist:
            raise InvalidTransition("A entrada exige IMEI/série e checklist físico.")
        if not context.has_technician:
            raise InvalidTransition("A OS precisa de um técnico responsável.")


class AnalyzingState(OrderState):
    status = OrderStatus.ANALYZING
    targets = frozenset({OrderStatus.AWAITING_APPROVAL})

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        super().validate(target, context)
        if not context.has_technician or not context.has_technical_report:
            raise InvalidTransition("Informe o técnico e o laudo antes de enviar o orçamento.")


class AwaitingApprovalState(OrderState):
    status = OrderStatus.AWAITING_APPROVAL
    targets = frozenset(
        {OrderStatus.ANALYZING, OrderStatus.AWAITING_PARTS, OrderStatus.REPAIRING}
    )

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        super().validate(target, context)
        if target == OrderStatus.ANALYZING:
            if context.approval_decision is not False:
                raise InvalidTransition("Registre a recusa do orçamento para voltar à análise.")
            return
        if context.approval_decision is not True or not context.quote_is_approved:
            raise InvalidTransition("O orçamento atual precisa de aprovação explícita.")
        expected = (
            OrderStatus.REPAIRING if context.stock_available else OrderStatus.AWAITING_PARTS
        )
        if target != expected:
            raise InvalidTransition("O próximo estado deve refletir a disponibilidade das peças.")


class AwaitingPartsState(OrderState):
    status = OrderStatus.AWAITING_PARTS
    targets = frozenset({OrderStatus.REPAIRING})

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        super().validate(target, context)
        if not context.quote_is_approved:
            raise InvalidTransition("O orçamento atual precisa estar aprovado.")
        if not context.stock_available:
            raise InvalidTransition("Ainda há peças sem estoque suficiente.")


class RepairingState(OrderState):
    status = OrderStatus.REPAIRING
    targets = frozenset({OrderStatus.COMPLETED})

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        super().validate(target, context)
        if not context.quote_is_approved:
            raise InvalidTransition("O orçamento atual precisa estar aprovado.")
        if not context.has_technical_report:
            raise InvalidTransition("A conclusão exige laudo técnico.")
        if not context.stock_available:
            raise InvalidTransition("Estoque insuficiente para concluir esta OS.")


class CompletedState(OrderState):
    status = OrderStatus.COMPLETED
    targets = frozenset({OrderStatus.DELIVERED})

    def validate(self, target: OrderStatus, context: StateContext) -> None:
        super().validate(target, context)
        if not context.fully_paid:
            raise InvalidTransition("A entrega exige pagamento integral.")


class DeliveredState(OrderState):
    status = OrderStatus.DELIVERED


STATES = {
    state.status: state
    for state in (
        IntakeState(), AnalyzingState(), AwaitingApprovalState(),
        AwaitingPartsState(), RepairingState(), CompletedState(), DeliveredState(),
    )
}


def transition_state(
    current: OrderStatus, target: OrderStatus, context: StateContext
) -> OrderStatus:
    return STATES[current].transition(target, context).status
