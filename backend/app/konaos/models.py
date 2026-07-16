"""Simplified Pydantic models for GPT-friendly API responses."""
from typing import Optional, List, Literal, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator


class EventSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Simplified event summary - only essential fields for GPT."""
    id: str
    name: str
    city: str
    state: str
    address_line1: Optional[str] = Field(None, alias="addressLine1")
    address_line2: Optional[str] = Field(None, alias="addressLine2")
    zip_code: Optional[str] = Field(None, alias="zipCode")
    full_address: Optional[str] = Field(None, alias="fullAddress")
    start_date_time: int = Field(alias="startDateTime")
    end_date_time: int = Field(alias="endDateTime")
    event_status: str = Field(alias="eventStatus")
    # Simplified staff - just names
    staff_names: List[str] = Field(default_factory=list)
    # Simplified assets - just names
    asset_names: List[str] = Field(default_factory=list)


class EventsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Response containing a list of events."""
    data: List[EventSummary]
    # totalCount is unreliable (often 0 even when events exist) - not included in model
    count: int  # Use this to determine number of events
    offset: int
    limit: int
    search_text: str = Field(alias="searchText")


class EventDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    """Detailed event information."""
    id: str
    name: str
    event_code: str = Field(alias="eventCode")
    link: str
    address_line1: str = Field(alias="addressLine1")
    address_line2: Optional[str] = Field(None, alias="addressLine2")
    city: str
    state: str
    zip_code: str = Field(alias="zipCode")
    country: str
    county: Optional[str] = None
    start_date_time: int = Field(alias="startDateTime")
    end_date_time: int = Field(alias="endDateTime")
    
    # Contact information (fields directly on event object)
    contact_name: Optional[str] = Field(None, alias="contactName")
    contact_email: Optional[str] = Field(None, alias="contactEmail")
    contact_phone: Optional[str] = Field(None, alias="contactPhoneNumber")
    contact_phone_country_code: Optional[str] = Field(None, alias="contactPhoneNumCountryCode")
    
    event_status: str = Field(alias="eventStatus")
    manual_status: str = Field(alias="manualStatus")
    activated: bool
    soft_deleted: bool = Field(alias="softDeleted")
    notes: Optional[str] = None
    admin_notes: Optional[str] = Field(None, alias="adminNotes")
    orders_count: int = Field(alias="ordersCount")
    
    # Staff and assets (keep as dict for flexibility with complex nested structures)
    event_staffs: List[dict] = Field(default_factory=list, alias="eventStaffsDtoList")
    event_assets: List[dict] = Field(default_factory=list, alias="eventAssetsDtoList")


class StaffInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Staff member information - simplified for GPT clarity."""
    id: str
    user_id: str = Field(alias="userId")
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    email: str
    phone: str = Field(alias="phoneNum")
    role_name: str = Field(alias="roleName")
    activated: bool


class StaffResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    """Response containing a list of staff members."""
    data: List[StaffInfo]
    # totalCount is unreliable - excluded from response
    count: int  # Use this to determine number of staff
    offset: int
    limit: int
    search_text: str = Field(alias="searchText")


class StaffAvailability(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Staff availability/unavailability record."""
    id: str
    user_id: str = Field(alias="userId")
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    start_date_time: int = Field(alias="startDateTime")
    end_date_time: int = Field(alias="endDateTime")
    start_date: Optional[str] = Field(None, alias="startDate")
    start_time: Optional[str] = Field(None, alias="startTime")
    end_date: Optional[str] = Field(None, alias="endDate")
    end_time: Optional[str] = Field(None, alias="endTime")
    available: bool = True  # True = available, False = unavailable
    all_day: bool = Field(False, alias="allDay")
    recurring_type: str = Field("", alias="recurringType")
    description: Optional[str] = ""
    days: Optional[str] = ""  # For recurring availability (e.g., "MON,TUE,WED")


class StaffAvailabilityResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Response containing staff availability records."""
    data: List[StaffAvailability]
    count: int
    offset: int
    limit: int


class StaffScheduleAvailabilitySlot(BaseModel):
    """One availability window from KonaOS staffs-schedule users-list (nested under each user)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
    created_by: str = Field(alias="createdBy")
    updated_by: str = Field(alias="updatedBy")
    deleted: bool
    user_id: str = Field(alias="userId")
    staff_availabilities_series_id: str = Field(alias="staffAvailabilitiesSeriesId")
    start_date_time: int = Field(alias="startDateTime")
    franchise_id: str = Field(alias="franchiseId")
    end_date_time: int = Field(alias="endDateTime")
    all_day: bool = Field(alias="allDay")
    expiry_date: int = Field(alias="expiryDate")
    recurring_type: str = Field(alias="recurringType")
    available: bool
    monthly_day: int = Field(alias="monthlyDay")
    description: str = ""
    last_day_of_month: bool = Field(alias="lastDayOfMonth")
    leave_applied: bool = Field(alias="leaveApplied")
    days: str = ""


class StaffScheduleUserRow(BaseModel):
    """Staff member row with nested availability slots (KonaOS users-list shape)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
    created_by: str = Field(alias="createdBy")
    updated_by: str = Field(alias="updatedBy")
    deleted: bool
    role_name: str = Field(alias="roleName")
    last_name: str = Field(alias="lastName")
    activated: bool
    user_id: str = Field(alias="userId")
    first_name: str = Field(alias="firstName")
    not_available_for_assign: bool = Field(alias="notAvailableForAssign")
    email: str
    brand_ids: List[str] = Field(alias="brandIds")
    staff_availabilities_list: List[StaffScheduleAvailabilitySlot] = Field(
        default_factory=list,
        alias="staffAvailabilitiesList",
    )

    @field_validator("staff_availabilities_list", mode="before")
    @classmethod
    def _null_list_to_empty(cls, v: Any) -> List[Any]:
        return v if v is not None else []


class StaffScheduleUsersListResponse(BaseModel):
    """Response from GET /api/v1/secure/staffs-schedule/users-list (proxied)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    sort_column: Optional[str] = Field(None, alias="sortColumn")
    count: int
    total_count: Optional[int] = Field(None, alias="totalCount")
    limit: int
    sort_type: Optional[str] = Field(None, alias="sortType")
    data: List[StaffScheduleUserRow]
    offset: int
    to_date: int = Field(0, alias="toDate")
    search_text: str = Field("", alias="searchText")
    from_date: int = Field(0, alias="fromDate")


class CreateEventRequest(BaseModel):
    """Request model for creating a new event.

    Extra fields are allowed, but only those that are real KonaOS quick-add
    payload keys survive — clientIndustriesTypeId, prePay, taxPercent,
    givebackPercentage pass through; invented ones (kurbsideEvent, driverNotes)
    are dropped in KonaosClient.create_event so KonaOS doesn't reject the whole
    body with "invalidJsonError". driverNotes is instead written via a
    follow-up event update once the new event id is known.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str = Field(..., description="Event name (required)")
    start_date_time: int = Field(..., alias="startDateTime", description="Event start date/time (Unix timestamp in milliseconds)")
    end_date_time: int = Field(..., alias="endDateTime", description="Event end date/time (Unix timestamp in milliseconds)")
    business_name: str = Field(..., alias="businessName", description="Business name (required)")
    address_line1: str = Field(..., alias="addressLine1", description="Street address (required)")
    city: str = Field(..., description="City (required)")
    state: str = Field(..., description="State (required)")
    zip_code: str = Field(..., alias="zipCode", description="Zip code (required)")
    contact_name: str = Field(..., alias="contactName", description="Contact name (required)")
    contact_email: str = Field(..., alias="contactEmail", description="Contact email (required)")
    brand_id: Optional[str] = Field(None, alias="brandId", description="Brand ID (optional, uses default if not provided)")
    client_id: Optional[str] = Field(None, alias="clientId", description="Client ID (optional)")
    contact_title: Optional[str] = Field("", alias="contactTitle", description="Contact title (optional)")
    contact_phone: Optional[str] = Field("", alias="contactPhone", description="Contact phone number (optional)")
    contact_phone_country_code: Optional[str] = Field("+1", alias="contactPhoneCountryCode", description="Phone country code (default: +1)")
    county: Optional[str] = Field("", description="County (optional)")
    country: Optional[str] = Field("USA", description="Country (default: USA)")
    admin_notes: Optional[str] = Field("", alias="adminNotes", description="Admin notes (optional)")
    notes: Optional[str] = Field("", description="Event notes (optional, can be HTML)")
    event_status: Optional[str] = Field("hold", alias="eventStatus", description="Event status (default: hold)")
    manual_status: Optional[str] = Field("pending", alias="manualStatus", description="Manual status (default: pending)")
    payment_term: Optional[str] = Field("menu", alias="paymentTerm", description="Payment term (default: menu)")


class UpdateEventRequest(BaseModel):
    """Request model for updating an existing event."""
    # Keep businessName mandatory, but allow passthrough of additional frontend keys
    # so new KonaOS fields are not silently dropped at proxy level.
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Required by KonaOS for update payload consistency.
    business_name: str = Field(..., alias="businessName", description="Business name (required)")

    # Common editable event fields.
    name: Optional[str] = Field(None, description="New event name (optional)")
    admin_notes: Optional[str] = Field(None, alias="adminNotes", description="New admin notes (optional)")
    notes: Optional[str] = Field(None, description="New event notes (optional, can be HTML)")
    address_line1: Optional[str] = Field(None, alias="addressLine1")
    address_line2: Optional[str] = Field(None, alias="addressLine2")
    city: Optional[str] = Field(None)
    state: Optional[str] = Field(None)
    county: Optional[str] = Field(None)
    zip_code: Optional[str] = Field(None, alias="zipCode")
    contact_name: Optional[str] = Field(None, alias="contactName")
    contact_email: Optional[str] = Field(None, alias="contactEmail")
    contact_phone_number: Optional[str] = Field(None, alias="contactPhoneNumber")
    contact_phone_num_country_code: Optional[str] = Field(None, alias="contactPhoneNumCountryCode")
    manual_status: Optional[str] = Field(None, alias="manualStatus")
    payment_term: Optional[str] = Field(None, alias="paymentTerm")
    start_date_time: Optional[int] = Field(None, alias="startDateTime")
    end_date_time: Optional[int] = Field(None, alias="endDateTime")
    recurring_type: Optional[str] = Field(None, alias="recurringType")
    event_type: Optional[str] = Field(None, alias="eventType")
    event_code: Optional[str] = Field(None, alias="eventCode")
    client_id: Optional[str] = Field(None, alias="clientId")
    brand_id: Optional[str] = Field(None, alias="brandId")

    # Sales and settlement fields.
    event_sales_type_id: Optional[str] = Field(None, alias="eventSalesTypeId")
    cash_amount: Optional[float] = Field(None, alias="cashAmount")
    check_amount: Optional[float] = Field(None, alias="checkAmount")
    cc_amount: Optional[float] = Field(None, alias="ccAmount")
    invoice_amount: Optional[float] = Field(None, alias="invoiceAmount")
    invoice_tax_amount: Optional[float] = Field(None, alias="invoiceTaxAmount")
    event_sales_collected: Optional[float] = Field(None, alias="eventSalesCollected")
    event_sales: Optional[float] = Field(None, alias="eventSales")
    collected: Optional[float] = Field(None)
    balance: Optional[float] = Field(None)
    sales_tax: Optional[float] = Field(None, alias="salesTax")
    tip_amount: Optional[float] = Field(None, alias="tipAmount")
    net_event_sales: Optional[float] = Field(None, alias="netEventSales")
    giveback_subtotal: Optional[float] = Field(None, alias="givebackSubtotal")
    giveback: Optional[float] = Field(None)
    giveback_paid: Optional[bool] = Field(None, alias="givebackPaid")

    # Asset associations.
    event_assets_list: Optional[List[dict]] = Field(None, alias="eventAssetsList")
    event_staff_list: Optional[List[dict]] = Field(None, alias="eventStaffList")
    event_templates_dto_list: Optional[List[dict]] = Field(None, alias="eventTemplatesDtoList")
    items_dto_list: Optional[List[dict]] = Field(None, alias="itemsDtoList")
    tags: Optional[List[dict]] = Field(None)
    event_banner_files: Optional[List[dict]] = Field(None, alias="eventBannerFiles")

    # Frequently used boolean controls in frontend update payloads.
    use_time_slot: Optional[bool] = Field(None, alias="useTimeSlot")
    remove_assets: Optional[bool] = Field(None, alias="removeAssets")
    update_series: Optional[bool] = Field(None, alias="updateSeries")


class DeleteEventRequest(BaseModel):
    """Request model for deleting an event."""
    update_series: Optional[bool] = Field(False, alias="updateSeries", description="Whether to update the entire series if this is a recurring event (default: False)")


class InvoiceMarkPaidRequest(BaseModel):
    """Body for KonaOS PUT .../update-invoice-status (mark as paid)."""

    model_config = ConfigDict(populate_by_name=True)

    partial_paid: Optional[bool] = Field(None, alias="partialPaid")
    paid_amount: Optional[float] = Field(None, alias="paidAmount")
    deposite_transaction: Optional[bool] = Field(None, alias="depositeTransaction")
    payment_note: Optional[str] = Field(None, alias="paymentNote")


class EventOperationResponse(BaseModel):
    """Response model for event create/update/delete operations."""
    general: List[dict] = Field(..., description="List of response messages")
    
    @property
    def success(self) -> bool:
        """Check if the operation was successful."""
        if not self.general:
            return False
        message_code = self.general[0].get("messageCode", "")
        return "success" in message_code.lower()
    
    @property
    def message(self) -> str:
        """Get the response message."""
        if not self.general:
            return ""
        return self.general[0].get("message", "")


class StaffBrandInput(BaseModel):
    """Brand mapping used by staff create endpoint."""
    brand_id: str = Field(alias="brandId")


class CreateStaffRequest(BaseModel):
    """Request model for creating a new staff member."""
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    first_name: str = Field(..., alias="firstName", description="Staff first name")
    last_name: str = Field(..., alias="lastName", description="Staff last name")
    email: str = Field(..., description="Staff email")
    phone_num: str = Field(..., alias="phoneNum", description="Primary phone number")
    num_country_code: str = Field("+1", alias="numCountryCode", description="Primary phone country code")
    staff_type: Optional[Literal["server", "manager", "worker", "driver"]] = Field(
        None,
        alias="staffType",
        description="Friendly role type. Used to resolve roleId if roleId is not provided.",
    )
    role_id: Optional[str] = Field(
        None,
        alias="roleId",
        description="KonaOS role ID. If omitted, it is resolved from staffType using env role mappings.",
    )
    alternate_num_country_code: str = Field("+1", alias="alternateNumCountryCode")
    alternate_phone_num: str = Field("", alias="alternatePhoneNum")
    emergency_contact_person_name: str = Field("", alias="emergencyContactPersonName")
    emergency_num_country_code: str = Field("+1", alias="emergencyNumCountryCode")
    emergency_phone_num: str = Field("", alias="emergencyPhoneNum")
    hourly_rate: str = Field("0", alias="hourlyRate")
    address: str = Field("", description="Street address")
    city: str = Field("", description="City")
    state: str = Field("", description="State")
    country: str = Field("USA", description="Country")
    zip_code: str = Field("", alias="zipCode")
    bio_image_file_id: str = Field("", alias="bioImageFileId")
    bio: str = Field("", description="Optional bio")
    staff_brand_list: List[StaffBrandInput] = Field(default_factory=list, alias="staffBrandList")
    access_group_permissions_input: Optional[Any] = Field(None, alias="accessGroupPermissionsInput")
    access_permissions_updated: bool = Field(False, alias="accessPermissionsUpdated")


class ClientInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Client information - simplified for GPT clarity."""
    id: str
    code: str
    business_name: str = Field(alias="businessName")
    client_name: str = Field(alias="clientName")
    email: str
    phone_num: str = Field(alias="phoneNum")
    phone_country_code: Optional[str] = Field(None, alias="numCountryCode")
    city: str
    state: str
    address: Optional[str] = None
    zip_code: Optional[str] = Field(None, alias="zipCode")
    county: Optional[str] = None
    country: Optional[str] = None
    client_industries_type_id: Optional[str] = Field(None, alias="clientIndustriesTypeId")
    activated: bool
    payment_term: Optional[str] = Field(None, alias="paymentTerm")
    admin_notes: Optional[str] = Field(None, alias="adminNotes")


class ClientDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    """Detailed client information from client details endpoint."""
    id: str
    code: Optional[str] = None
    business_name: Optional[str] = Field(None, alias="businessName")
    client_name: Optional[str] = Field(None, alias="clientName")
    email: Optional[str] = None
    phone_num: Optional[str] = Field(None, alias="phoneNum")
    num_country_code: Optional[str] = Field(None, alias="numCountryCode")
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = Field(None, alias="zipCode")
    county: Optional[str] = None
    country: Optional[str] = None
    client_industries_type_id: Optional[str] = Field(None, alias="clientIndustriesTypeId")
    activated: Optional[bool] = None
    payment_term: Optional[str] = Field(None, alias="paymentTerm")
    admin_notes: Optional[str] = Field(None, alias="adminNotes")


class ClientResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    """Response containing a list of clients."""
    data: List[ClientInfo]
    # totalCount is unreliable - excluded from response
    count: int  # Use this to determine number of clients
    offset: int
    limit: int
    search_text: str = Field(alias="searchText")


class SalesDataRequest(BaseModel):
    """Request model for sales data report endpoint."""
    limit: int = Field(10, ge=1, le=100, description="Number of results per page")
    offset: int = Field(0, ge=0, description="Pagination offset")
    sort_column: str = Field("", alias="sortColumn", description="Column to sort by")
    sort_type: str = Field("desc", alias="sortType", description="Sort direction (asc/desc)")
    search_text: str = Field("", alias="searchText", description="Search text")
    from_date: int = Field(..., alias="fromDate", description="Start date timestamp (Unix epoch in milliseconds)")
    to_date: int = Field(..., alias="toDate", description="End date timestamp (Unix epoch in milliseconds)")
    client_id: str = Field("", alias="clientId", description="Client ID filter")
    industry_type_id_list: List[str] = Field(default_factory=lambda: ["ALL"], alias="industryTypeIdList")
    manual_status: List[str] = Field(
        default_factory=lambda: ["ALL"],
        alias="manualStatus",
        description='Manual status filter. Defaults to ["ALL"] to match KonaOS browser requests.'
    )
    brand: Literal["konaice", "travelintom", "both"] = Field(
        "both",
        description='Brand selector. Allowed values: "konaice", "travelintom", "both".'
    )


class SalesDataResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    """Response model for sales data report endpoint."""
    # Upstream KonaOS may return null for sortColumn/sortType; allow Optional and
    # default to empty strings so the proxy never fails validation on these.
    sort_column: Optional[str] = Field("", alias="sortColumn")
    count: int
    total_count: Optional[int] = Field(None, alias="totalCount")
    limit: int
    sort_type: Optional[str] = Field("", alias="sortType")
    data: List[dict] = Field(default_factory=list)
    offset: int = 0
    search_text: str = Field("", alias="searchText")


class ClientRankingRequest(BaseModel):
    """Request model for client ranking report endpoint."""
    limit: int = Field(10, ge=1, le=100, description="Number of results per page")
    offset: int = Field(0, ge=0, description="Pagination offset")
    sort_column: str = Field("", alias="sortColumn", description="Column to sort by")
    sort_type: str = Field("asc", alias="sortType", description="Sort direction (asc/desc)")
    search_text: str = Field("", alias="searchText", description="Search text")
    from_date: int = Field(..., alias="fromDate", description="Start date timestamp (Unix epoch in milliseconds)")
    to_date: int = Field(..., alias="toDate", description="End date timestamp (Unix epoch in milliseconds)")
    client_id: str = Field("", alias="clientId", description="Client ID filter")
    industry_type_id_list: List[str] = Field(default_factory=lambda: ["ALL"], alias="industryTypeIdList")
    brand: Literal["konaice", "travelintom", "both"] = Field(
        "both",
        description='Brand selector. Allowed values: "konaice", "travelintom", "both".'
    )


class ClientRankingResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    """Response model for client ranking report endpoint."""
    sort_column: str = Field("", alias="sortColumn")
    count: int
    total_count: Optional[int] = Field(None, alias="totalCount")
    limit: int
    sort_type: str = Field("", alias="sortType")
    data: List[dict] = Field(default_factory=list)
    offset: int = 0
    search_text: str = Field("", alias="searchText")


class ClientIndustryType(BaseModel):
    model_config = ConfigDict(extra="ignore")
    """Client industry type information."""
    id: str
    type: str
    created_at: Optional[int] = Field(None, alias="createdAt")
    updated_at: Optional[int] = Field(None, alias="updatedAt")


class ZipcodeWebsiteResponse(BaseModel):
    """Response model for scraped brand-specific truck finder page content."""
    zipcode: str
    content_markdown: str = Field(alias="contentMarkdown", description="Scraped page content in markdown format")
    emails: List[str] = Field(default_factory=list, description="Email addresses found on the page")
    phone_numbers: List[str] = Field(default_factory=list, alias="phoneNumbers", description="Phone numbers found on the page")
    brand: str = Field(default="Kona_ice", description="Brand that was searched (Kona_ice or Travelin_tom)")