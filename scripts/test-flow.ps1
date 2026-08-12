<#
    test-flow.ps1
    ---------------------------------------------------------------------------
    Walks the whole ITS RMS POC business flow end to end and prints every
    API response.

        1. Log in as the fictional multi-role user (+919999999999)
        2. Select the "Data Entry" role
        3. Create a sample medical camp
        4. Register a fictional patient      -> publishes PatientRegistered
        5. Wait for clinical-service to open a case from that event
        6. Book a consultation slot          -> publishes SlotBooked
        7. Record vitals                     -> publishes VitalsRecorded
        8. Record a diagnosis
        9. Close the case                    -> publishes CaseClosed

    Everything created here is FICTIONAL sample data.

    Run it from the project root, after `docker compose up -d --build`:

        powershell -ExecutionPolicy Bypass -File .\scripts\test-flow.ps1

    Works in Windows PowerShell 5.1 and PowerShell 7+.
#>

[CmdletBinding()]
param(
    [string]$IdentityUrl = 'http://localhost:8001',
    [string]$CampUrl     = 'http://localhost:8002',
    [string]$ClinicalUrl = 'http://localhost:8003',
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$script:StepNumber = 0

# ---------------------------------------------------------------- helpers ---
function Write-Step {
    param([string]$Title)
    $script:StepNumber++
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkGray
    Write-Host (" STEP {0}  {1}" -f $script:StepNumber, $Title) -ForegroundColor Cyan
    Write-Host ('=' * 78) -ForegroundColor DarkGray
}

function Write-Response {
    param([string]$Label, $Object)
    Write-Host ("--> {0}" -f $Label) -ForegroundColor Green
    ($Object | ConvertTo-Json -Depth 10) | Write-Host
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Uri,
        $Body
    )
    $params = @{
        Method      = $Method
        Uri         = $Uri
        ContentType = 'application/json'
    }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
    }
    try {
        return Invoke-RestMethod @params
    }
    catch {
        Write-Host ("HTTP call failed: {0} {1}" -f $Method, $Uri) -ForegroundColor Red
        # PowerShell 7 puts the response body here...
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            Write-Host $_.ErrorDetails.Message -ForegroundColor Red
        }
        # ...Windows PowerShell 5.1 makes you read the stream yourself.
        else {
            $resp = $_.Exception.Response
            if ($resp -and ($resp | Get-Member -Name GetResponseStream -MemberType Method)) {
                try {
                    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
                    Write-Host $reader.ReadToEnd() -ForegroundColor Red
                }
                catch { }
            }
        }
        throw
    }
}

function Wait-ForService {
    param([string]$Name, [string]$Url, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "$Url/health" -TimeoutSec 5
            if ($health.status -eq 'ok') {
                Write-Host ("   {0,-18} healthy   (rabbitmqConnected = {1})" -f $Name, $health.rabbitmqConnected) -ForegroundColor Green
                return $health
            }
        }
        catch {
            # Service not listening yet - that is expected while the stack boots.
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name at $Url did not become healthy within $TimeoutSeconds seconds. Is 'docker compose up' running?"
}

function Wait-ForCase {
    param([string]$PatientId, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $result = Invoke-RestMethod -Method Get -Uri "$ClinicalUrl/cases?patient_id=$PatientId"
        if ($result.count -gt 0) { return $result.cases[0] }
        Start-Sleep -Milliseconds 500
    }
    throw "clinical-service never opened a case for $PatientId. Check the RabbitMQ connection and 'docker compose logs clinical-service'."
}

# =============================================================================
Write-Host ''
Write-Host '  ITS RMS EventCatalog POC - end-to-end flow' -ForegroundColor White
Write-Host '  All data created by this script is fictional sample data.' -ForegroundColor DarkYellow

Write-Step 'Waiting for all three services to report healthy'
Wait-ForService -Name 'identity-service' -Url $IdentityUrl -TimeoutSeconds $StartupTimeoutSeconds | Out-Null
Wait-ForService -Name 'camp-service'     -Url $CampUrl     -TimeoutSeconds $StartupTimeoutSeconds | Out-Null
Wait-ForService -Name 'clinical-service' -Url $ClinicalUrl -TimeoutSeconds $StartupTimeoutSeconds | Out-Null

# ---------------------------------------------------------------- 1. login ---
Write-Step 'Log in as the fictional multi-role user (+919999999999)'
$login = Invoke-Api -Method Post -Uri "$IdentityUrl/login" -Body @{ phone = '+919999999999' }
Write-Response -Label 'POST /login' -Object $login
Write-Host ("    This phone holds {0} roles: {1}" -f $login.availableRoles.Count, ($login.availableRoles -join ', ')) -ForegroundColor Yellow
Write-Host '    identity-service published UserLoggedIn (routing key: user.logged-in)' -ForegroundColor DarkGray

# ----------------------------------------------------------- 2. role select ---
Write-Step 'Select the "Data Entry" role for this session'
$role = Invoke-Api -Method Post -Uri "$IdentityUrl/select-role" -Body @{
    sessionToken = $login.sessionToken
    role         = 'Data Entry'
}
Write-Response -Label 'POST /select-role' -Object $role

# ----------------------------------------------------------- 3. create camp ---
Write-Step 'Create a sample medical camp'
$camp = Invoke-Api -Method Post -Uri "$CampUrl/camps" -Body @{
    campName            = 'Mumbai Community Health Camp (fictional)'
    hostCity            = 'Mumbai'
    location            = 'Sample Markaz Hall, Demo Road (fictional address)'
    startDate           = '2026-09-10'
    endDate             = '2026-09-13'
    timezone            = 'Asia/Kolkata'
    departmentsOffered  = @('General Medicine', 'Dental', 'Ophthalmology')
}
Write-Response -Label 'POST /camps' -Object $camp

Write-Host ''
Write-Host '--> GET /camps/availability (what a patient would see)' -ForegroundColor Green
$availability = Invoke-Api -Method Get -Uri "$CampUrl/camps/availability"
($availability | ConvertTo-Json -Depth 10) | Write-Host

# ------------------------------------------------------- 4. register patient ---
Write-Step 'Register a fictional patient'
$patient = Invoke-Api -Method Post -Uri "$CampUrl/patients/register" -Body @{
    patientName  = 'Zainab Demo (fictional)'
    phone        = '+919777777777'
    campId       = $camp.campId
    patientType  = 'ITS member'
    registeredBy = $login.userId
}
Write-Response -Label 'POST /patients/register' -Object $patient
Write-Host '    camp-service published PatientRegistered (routing key: patient.registered)' -ForegroundColor DarkGray

# ------------------------------------------------ 5. case opened by the event ---
Write-Step 'Wait for clinical-service to open a case from the PatientRegistered event'
$case = Wait-ForCase -PatientId $patient.patientId
Write-Response -Label ("GET /cases?patient_id={0}" -f $patient.patientId) -Object $case
$caseId = $case.caseId
Write-Host ("    clinical-service consumed PatientRegistered and created {0}" -f $caseId) -ForegroundColor Yellow

# ------------------------------------------------------------- 6. book slot ---
Write-Step 'Book a consultation slot'
$slot = Invoke-Api -Method Post -Uri "$CampUrl/slots/book" -Body @{
    patientId  = $patient.patientId
    campId     = $camp.campId
    department = 'General Medicine'
    date       = '2026-09-11'
    session    = 'Morning'
}
Write-Response -Label 'POST /slots/book' -Object $slot
Write-Host '    camp-service published SlotBooked (routing key: slot.booked)' -ForegroundColor DarkGray

Start-Sleep -Seconds 2
Write-Host ''
Write-Host '--> The slot should now be attached to the case by the consumer' -ForegroundColor Green
$case = Invoke-Api -Method Get -Uri "$ClinicalUrl/cases/$caseId"
($case | ConvertTo-Json -Depth 10) | Write-Host

# ---------------------------------------------------------------- 7. vitals ---
Write-Step 'Doctor records vitals'
$vitals = Invoke-Api -Method Post -Uri "$ClinicalUrl/cases/$caseId/vitals" -Body @{
    temperature    = 37.2
    bloodPressure  = '120/80'
    weight         = 68.5
    notes          = 'Patient reports mild headache for three days (fictional)'
    recordedBy     = 'usr-1002'
}
Write-Response -Label "POST /cases/$caseId/vitals" -Object $vitals
Write-Host '    clinical-service published VitalsRecorded (routing key: vitals.recorded)' -ForegroundColor DarkGray

# ------------------------------------------------------------- 8. diagnosis ---
Write-Step 'Doctor records a diagnosis'
$diagnosis = Invoke-Api -Method Post -Uri "$ClinicalUrl/cases/$caseId/diagnosis" -Body @{
    chiefComplaint = 'Headache and fatigue for 3 days (fictional)'
    diagnosis      = 'Tension headache (fictional)'
    medication     = 'Paracetamol 500mg, twice daily for 3 days (fictional)'
    followUpDate   = '2026-09-25'
    recordedBy     = 'usr-1002'
}
Write-Response -Label "POST /cases/$caseId/diagnosis" -Object $diagnosis

# ------------------------------------------------------------ 9. close case ---
Write-Step 'Doctor closes the case'
$closed = Invoke-Api -Method Post -Uri "$ClinicalUrl/cases/$caseId/close" -Body @{
    closedBy     = 'usr-1002'
    closingNotes = 'Consultation complete, patient advised rest (fictional)'
}
Write-Response -Label "POST /cases/$caseId/close" -Object $closed
Write-Host '    clinical-service published CaseClosed (routing key: case.closed)' -ForegroundColor DarkGray

# -------------------------------------------------------------- 10. summary ---
Write-Step 'Flow complete'
Write-Host ''
Write-Host '  Events that travelled through the its.rms.events topic exchange:' -ForegroundColor White
Write-Host '    identity-service  --[user.logged-in]-->     camp-service (log only)'
Write-Host '    camp-service      --[patient.registered]--> clinical-service (case opened)'
Write-Host '    camp-service      --[slot.booked]-->        clinical-service (slot attached)'
Write-Host '    clinical-service  --[vitals.recorded]-->    (no consumer in this POC)'
Write-Host '    clinical-service  --[case.closed]-->        (no consumer in this POC)'
Write-Host ''
Write-Host '  See it for yourself:' -ForegroundColor White
Write-Host '    docker compose logs camp-service clinical-service | Select-String "PUBLISH|CONSUME|FLOW"'
Write-Host '    RabbitMQ UI:  http://localhost:15672   (guest / guest)'
Write-Host ''
Write-Host ("  Final case {0} status: {1}" -f $closed.caseId, $closed.status) -ForegroundColor Green
Write-Host ''
