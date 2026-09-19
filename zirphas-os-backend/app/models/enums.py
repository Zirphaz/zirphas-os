from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    TECH = "tech"
    FRONT_DESK = "front_desk"


class OrderStatus(str, Enum):
    INTAKE = "intake"
    ANALYZING = "analyzing"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_PARTS = "awaiting_parts"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    DELIVERED = "delivered"


STATUS_LABELS = {
    OrderStatus.INTAKE: "Entrada",
    OrderStatus.ANALYZING: "Em Análise",
    OrderStatus.AWAITING_APPROVAL: "Aguardando Aprovação",
    OrderStatus.AWAITING_PARTS: "Aguardando Peça",
    OrderStatus.REPAIRING: "Na Bancada",
    OrderStatus.COMPLETED: "Concluído",
    OrderStatus.DELIVERED: "Entregue",
}
