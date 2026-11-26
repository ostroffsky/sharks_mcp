"""stdio MCP server for a single local Sharks Kids account."""
from datetime import date
from threading import Lock

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import PositiveInt

from club import session
from credentials import load

mcp = FastMCP("HC Sharks Kids")
read = ToolAnnotations(readOnlyHint=True)
write = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
booking_lock = Lock()


@mcp.tool(annotations=read)
def get_schedule(start_date: date | None = None, end_date: date | None = None,
                 student_id: PositiveInt | None = None) -> dict:
    """Расписание тренировок. Даты включительно, по умолчанию семь дней начиная с сегодня.
    Для семейного аккаунта укажите student_id. Без него сервер подскажет доступные профили.
    """
    with session(*load()) as club:
        return club.lessons(start_date, end_date, student_id, mine=False)


@mcp.tool(annotations=read)
def get_my_bookings(start_date: date | None = None, end_date: date | None = None,
                    student_id: PositiveInt | None = None) -> dict:
    """Занятия выбранного ученика по фильтру кабинета «Мои». Период включительно, по умолчанию семь дней."""
    with session(*load()) as club:
        return club.lessons(start_date, end_date, student_id, mine=True)


@mcp.tool(annotations=read)
def get_subscriptions(student_id: PositiveInt | None = None, include_inactive: bool = False) -> dict:
    """Абонементы ученика, включая семейные. end_date — дата окончания или null, если кабинет её не указал.
    По умолчанию только активные. Для истории передайте include_inactive=true.
    """
    with session(*load()) as club:
        return club.subscriptions(student_id, include_inactive)


@mcp.tool(annotations=write)
def book_training(lesson_id: PositiveInt, student_id: PositiveInt | None = None,
                  allow_time_overlap: bool = False) -> dict:
    """Записать ученика на выбранную групповую тренировку по lesson_id из расписания.
    Если для записи требуется вступление в доступную группу, кабинет создаёт заявку с этим занятием.
    Передавайте allow_time_overlap=true только по просьбе ученика записаться несмотря на пересечение.
    """
    with booking_lock, session(*load()) as club:
        return club.book(lesson_id, student_id, allow_time_overlap)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def cancel_booking(lesson_id: PositiveInt, student_id: PositiveInt | None = None) -> dict:
    """Отменить запись на одну тренировку по lesson_id из get_my_bookings.
    Для семейного аккаунта укажите student_id. Срок отмены задаёт клуб.
    """
    with booking_lock, session(*load()) as club:
        return club.cancel(lesson_id, student_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
