/**
 * Full text of the WWC "Preparing for Your Upcoming Hospital Procedure"
 * handout, rendered inline. Mirrors the structure of
 * OfficeProcedureInstructions so the two feel consistent.
 */
export default function HospitalProcedureInstructions() {
  return (
    <div className="space-y-4 text-[13px] leading-relaxed text-plum-ink">
      <header className="text-center pb-2 border-b border-plum-100">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium">
          Waldorf Women's Care · WWC Gynecology &amp; Aesthetics
        </div>
        <h3 className="font-serif text-[18px] text-plum-ink font-semibold tracking-tight mt-1">
          Preparing for Your Upcoming Hospital Procedure
        </h3>
      </header>

      <p>Thank you for entrusting your medical and surgical care to Waldorf Women's Care.</p>
      <p>
        Preparing for surgery can be a stressful process. It requires
        coordination between you, the patient, your doctor, the hospital
        staff, an anesthesiologist, your primary care doctor or specialist,
        and your insurance company. We do our best to make the process as
        easy as possible, but some tasks will have to be completed by you,
        the patient.
      </p>
      <p>
        This document will help to guide you through this process. If any
        aspect of the process is unclear, please message our Surgical
        Coordinator at{' '}
        <a className="underline" href="https://patient.klara.com" target="_blank" rel="noreferrer">
          patient.klara.com
        </a>{' '}
        (preferred) or call{' '}
        <a className="underline" href="tel:2402522140">240-252-2140</a>{' '}
        and she will gladly work with you to resolve the issue.
      </p>

      <HospitalContactTable />

      <CoordinatorTable />

      <Section title="Important Notices" defaultOpen>
        <SubHead>Medications</SubHead>
        <p>
          If you are taking medication(s), which you have discussed with
          your doctor, please continue to take your medication(s) as
          instructed. However, if you are taking blood thinners or aspirin,
          please discontinue using these medications as instructed by your
          doctor, as they may lead to increased blood loss during your
          procedure. <strong>Stop all GLP-1 medications 1 week
          (7 days) before your surgery</strong>, or it will be canceled by
          the anesthesiologist.
        </p>

        <SubHead>Allergies</SubHead>
        <p>
          If you have any allergies to medications, latex, or betadine,
          please alert your doctor as soon as possible.
        </p>

        <SubHead>What to bring</SubHead>
        <p>
          While at the hospital, you may opt to bring some comforts from
          home. You may bring reading material, slippers, an iPad, or a
          laptop computer. Toiletries are provided, but you are welcome to
          bring your own.
        </p>

        <SubHead>Work Note / FMLA</SubHead>
        <p>
          If work/school requires any documentation for time off due to
          your procedure, please keep in mind that it takes up to one week
          to be completed. The fee for completion of the document is{' '}
          <strong>$20</strong> to be paid to Waldorf Women's Care. We can
          accept forms via the patient portal, fax, email to{' '}
          <a className="underline" href="mailto:surgery@waldorfwomenscare.com">
            surgery@waldorfwomenscare.com
          </a>
          , or by dropping off in person. Please confirm that the forms
          have been received by the surgery coordinator{' '}
          (<a className="underline" href="tel:2402527862">240-252-7862</a>).
          A phone call or patient portal message will be issued to you once
          the forms are completed, but please contact your surgical
          coordinator if you have any questions or concerns.
        </p>
      </Section>

      <Section title="Your Pre-Op Appointment" defaultOpen>
        <p>
          The provider will discuss your procedure in detail. The provider
          will also review the risks, possible complications, and your
          recovery period. At the end of your appointment, your provider
          will answer any questions. You will receive a text message from
          our "Klara" messaging system to pay your surgical responsibility
          and to choose your surgical date and location. If your pre-op
          appointment is in the office, you will sign the surgical consent
          during your visit. However, if your pre-op appointment is
          virtual, you will need to sign your surgical consent via your
          patient portal. Go to{' '}
          <a className="underline" href="https://wwc.ema.md" target="_blank" rel="noreferrer">
            wwc.ema.md
          </a>{' '}
          to read and sign your consent.
        </p>
      </Section>

      <Section title="After Your Pre-Op Appointment" defaultOpen>
        <SubHead>Surgery Scheduling Notification</SubHead>
        <p>
          Within 7 days after your pre-op appointment, you will receive a
          text message from our "Klara" messaging system with instructions
          on how to move forward with scheduling your surgery.
        </p>

        <SubHead>Medical Clearance</SubHead>
        <p>
          Some patients require medical clearance before surgery. A medical
          clearance is a letter from your doctor (ex: primary care doctor
          or cardiologist) stating that it is safe for you to have the
          surgery. It also provides guidance to your doctor and the
          anesthesiologist on special requirements that need to be followed
          for you to have the surgery safely. If your surgeon or anesthesia
          team determines that you need to have medical clearance, schedule
          an appointment with your doctor and obtain a letter stating
          whether you are cleared for the surgery. You must submit this
          letter to the surgery coordinator{' '}
          <strong>five (5) business days</strong> before your surgery. You
          may submit the letter in person or have your doctor fax it to{' '}
          <a className="underline" href="tel:2402522141">240-252-2141</a>.
          If your doctor's office is responsible for faxing this medical
          clearance, please follow up with the primary care/specialist to
          confirm that the clearance was sent. Then you should contact the
          surgical coordinator to confirm that she received the medical
          clearance. If we do not receive the medical clearance letter in
          advance (five business days), your surgery will have to be
          cancelled and rescheduled.
        </p>

        <SubHead>EKG / ECG</SubHead>
        <p>
          In some cases, the doctor will have mentioned to you that we
          require an EKG before surgery. If you do not require medical
          clearance, but require an EKG, please call and schedule an
          appointment with your primary care provider/cardiologist to have
          a pre-operative EKG performed. Please ensure a copy of the EKG
          result is received by the surgical coordinator at Waldorf
          Women's Care. It can be delivered in person or faxed to{' '}
          <a className="underline" href="tel:2402522141">240-252-2141</a>.
        </p>

        <SubHead>Insurance and Financial Responsibility</SubHead>
        <p>
          Your financial responsibility is a calculation of your
          deductible, copay, and/or coinsurance. You must pay your
          financial responsibility before securing a surgical date. We
          encourage our patients to call their insurance company to verify
          their financial responsibility prior to surgery (the information
          is on the back of your insurance card). They will need the
          surgery CPT code and the diagnosis code(s), also known as ICD-10
          codes. If you are unable to make a full payment, contact your
          surgery coordinator to ask about a payment plan.
        </p>
        <p>
          <strong>Important Insurance Note:</strong> The financial
          responsibility calculation is for the surgeon — not the
          hospital/surgery center. The hospital may charge additional fees.
          If you would like an explanation of hospital charges, you should
          contact your insurance company, provide them with the procedure
          information, and ask for "hospital fees for
          inpatient/outpatient procedures."
        </p>

        <SubHead>Referral</SubHead>
        <p>
          You will be notified if your insurance requires a referral from
          your primary care provider to be covered for your procedure.
          Waldorf Women's Care would like to have a copy of this referral
          one week before surgery. In the event you need a referral from
          your primary care provider for your procedure, please call the
          primary care doctor's office and let them know what surgery you
          are scheduled for, as well as why you are having surgery, and
          explain that you require the referral for insurance to cover your
          procedure. If you are having difficulty obtaining this referral,
          you need to contact your surgical coordinator right away. If we
          do not receive this referral, your surgery may be cancelled.
        </p>

        <SubHead>Cancellations &amp; Reschedules</SubHead>
        <p>
          All cancellations and reschedules must be made with the Waldorf
          Women's Care/WWC Gynecology &amp; Aesthetics surgery coordinator
          (the contact information is listed in the above table).
        </p>
        <Warn>
          Do not cancel your procedure through the receptionist or through
          the patient portal. You may only cancel by contacting the surgery
          coordinator at{' '}
          <a className="underline" href="tel:2402527862">240-252-7862</a>{' '}
          or emailing{' '}
          <a className="underline" href="mailto:surgery@waldorfwomenscare.com">
            surgery@waldorfwomenscare.com
          </a>.
        </Warn>
        <p>
          There is a lot that goes into scheduling your procedure, so if a
          procedure must be canceled, the surgery coordinator must be
          contacted immediately. The surgical coordinator will, in turn,
          alert your doctor, and any other necessary entity. Waldorf
          Women's Care/WWC Gynecology &amp; Aesthetics reserves the right
          to charge a fee of <strong>$351</strong> for all
          surgeries/procedures not canceled within{' '}
          <strong>14 days</strong> in advance, absent a compelling reason,
          and at our sole discretion. This fee is not covered by insurance
          and must be paid before scheduling your next appointment.
        </p>
      </Section>

      <Section title="One Week Before Your Surgery" defaultOpen>
        <SubHead>Pre-Op Labs</SubHead>
        <p>
          Pre-Op labs are drawn by the phlebotomist and evaluated by your
          surgeon, the anesthesiologist, and hospital staff to determine if
          there are any issues that need to be addressed before your
          surgery. These labs must be drawn{' '}
          <strong>no earlier than seven (7) days</strong> before your
          scheduled surgery (per hospital rules and regulations). We can
          draw your labs in our office and report them to the hospital, or
          in some cases, the hospital draws the labs for you. Labs can be
          drawn in our office anytime during business hours. It is always a
          good idea to call ahead to the front desk to confirm phlebotomist
          availability. The lab hours are Monday through Friday between
          8:30 am and 4:30 pm. You may also have your labs drawn at a
          community lab facility, such as LabCorp.
        </p>
      </Section>

      <Section title="About One Week Before Your Surgery" defaultOpen>
        <SubHead>Transportation Preparation</SubHead>
        <p>
          Please make sure that you have a designated driver to take you to
          and from the hospital for your procedure.
        </p>
        <Warn>
          You may not drive yourself home or use a ride-share service
          (Uber, Lyft, Taxi) after your procedure. The person picking you
          up will need to meet you in the recovery area to sign you out
          before you are released. The person picking you up will then be
          asked to bring their car to the front door of the hospital to
          pick you up.
        </Warn>

        <SubHead>Medications</SubHead>
        <p>
          If you are taking any medications that are considered blood
          thinners or GLP-1 used for weight loss and diabetes, when to stop
          these medications should have been discussed with you by your
          surgeon and/or by the hospital nurse at the pre-operative
          appointment. If you are still unsure about a specific medication,
          contact the surgical coordinator as soon as possible.
        </p>
        <BloodThinnersTable />
      </Section>

      <Section title="The Night Before Your Procedure" defaultOpen>
        <p>
          It is normal to be nervous the night before your procedure. We
          understand the emotional stress this can cause and assure you
          that we want your procedure to go very smoothly and your recovery
          to go well. Keep in mind that you must{' '}
          <strong>NOT eat or drink anything after 12:00 am</strong> the
          night before your procedure. You should have a bag packed with
          something to make you more comfortable, such as a book,
          earphones, a magazine, etc.
        </p>
        <p>
          The hospital will ask you to shower the night before using
          unscented soap and/or an antibacterial soap or cleanser. After
          the shower, you will be asked to skip any application of lotion,
          body oil, deodorant, or perfume. Please leave all jewelry at home.
        </p>
      </Section>

      <Section title="The Day of Your Procedure" defaultOpen>
        <p>
          Please wake up in enough time to coordinate with your driver for
          your procedure. Keep in mind you need to check in{' '}
          <strong>two hours before</strong> your procedure starts. You
          should wear comfortable clothes on the day of your procedure. If
          you are having an inpatient procedure, be sure to pack
          comfortable clothes to wear home.
        </p>
        <p>
          A nurse will be the first person you see to start the IV and give
          you a hospital gown to put on. The anesthesiologist and your
          surgeon will meet with you and answer any final questions from
          you or your family.
        </p>
        <p>
          After the procedure is completed, you will wake up in the PACU
          (post-anesthesia care unit), also known as the recovery room. You
          may feel groggy and possibly nauseated. You may also have
          abdominal pain and cramping, depending on what type of surgery
          was performed. You will receive pain medication through your IV
          to help to make you more comfortable. You will be evaluated by
          the anesthesia provider and your post-operative nurse. Once you
          are deemed stable from surgery, you will be released to go home
          with your friend or family member and may be given prescriptions
          for pain medications (if appropriate). If you are having
          inpatient surgery, you will be transported to your hospital room
          for the rest of your recovery.
        </p>
      </Section>

      <Section title="After Your Surgery" defaultOpen>
        <p>
          If a breathing tube was placed in your throat during the
          procedure, you may experience some throat soreness. You may feel
          some abdominal and/or pelvic pain and cramping after your
          procedure. The medications prescribed for you should help
          decrease this discomfort. Please call{' '}
          <a className="underline" href="tel:2402522140">240-252-2140</a>{' '}
          if you are experiencing any of the symptoms listed below. If you
          need to speak with a doctor after hours for an emergency, call{' '}
          <a className="underline" href="tel:2402522140">240-252-2140</a>,
          and press option <strong>#7</strong> to be connected with the
          on-call doctor:
        </p>
        <ul className="list-disc pl-5 space-y-1">
          <li>Fever of 100.4°F or greater</li>
          <li>Worsening pelvic pain</li>
          <li>Nausea and/or vomiting</li>
          <li>Malodorous and/or greenish abnormal vaginal discharge</li>
          <li>Heavy bleeding</li>
          <li>You have not urinated within 6 hours after arriving home</li>
        </ul>
      </Section>

      <Section title="One – Two Weeks Post Procedure" defaultOpen>
        <p>
          Your surgeon would like to see you in the office after your
          procedure. The provider should have informed you of the specific
          timeframe to return to the office for a post-op visit. This
          information will also be in your discharge paperwork from the
          hospital. This post-op visit date varies based on the type of
          surgery performed. If you have not been scheduled for a post-op
          appointment, call your surgical coordinator to schedule your
          post-op appointment.
        </p>
      </Section>

      <Section title="Call Our Office If You Experience These Symptoms" defaultOpen tone="danger">
        <p className="text-rose-700 font-medium">
          Contact our office at{' '}
          <a className="underline" href="tel:2402522140">240-252-2140</a>.
        </p>
        <ul className="list-disc pl-5 space-y-1">
          <li>
            <u>If you are calling after hours, choose option 7</u> to
            contact the Answering Service, which will, in turn, contact the
            provider on call.
          </li>
          <li>
            If you are calling during business hours, ask to speak to a
            manager or provider if you are experiencing any of the
            following symptoms:
          </li>
        </ul>
        <div className="border border-rose-200 rounded-lg p-3 bg-rose-50/40">
          <ul className="list-disc pl-5 space-y-1 text-rose-700 font-medium">
            <li>
              You have cramping or pain not controlled with Ibuprofen or
              Tylenol.{' '}
              <span className="text-emerald-700 font-normal">
                You may experience cramping for one (1) week post-procedure.
              </span>
            </li>
            <li>
              You have heavy vaginal bleeding — soaking a pad every one to
              two hours.{' '}
              <span className="text-emerald-700 font-normal">
                Some spotting to light bleeding may persist for several
                weeks.
              </span>
            </li>
            <li>You develop a fever over 100.4 degrees.</li>
            <li>You develop a foul-smelling vaginal discharge.</li>
            <li>You experience nausea or vomiting.</li>
            <li>You have not urinated within 6 hours after arriving home.</li>
            <li>You are having chest pain.</li>
            <li>You are experiencing shortness of breath.</li>
            <li>You are experiencing swelling of the face and tongue.</li>
            <li>You are having suicidal or homicidal thoughts.</li>
          </ul>
        </div>
        <p className="text-center text-rose-700 font-semibold pt-1">
          In case of a life-threatening emergency, call{' '}
          <a className="underline" href="tel:911">911</a> or go to the
          nearest hospital emergency department.
        </p>
      </Section>

      <Section title="Checklist for a Successful and Smooth Experience">
        <ul className="list-disc pl-5 space-y-1">
          <li>
            If I'm having a NovaSure (Endometrial Ablation), I have
            confirmed that the result of my pathology report from my
            D&amp;C or Endometrial Biopsy was normal.
          </li>
          <li>Complete pre-op appointment.</li>
          <li>Sign your procedure consent.</li>
          <li>
            Pay your estimated financial responsibility. You may call your
            insurance to verify your patient's responsibility.
          </li>
          <li>Schedule your procedure.</li>
          <li>Schedule the day off from work.</li>
          <li>Make arrangements for your children/dependents.</li>
          <li>
            Notify your loved ones of your procedure so they may assist you
            during your recovery period.
          </li>
          <li>
            Make arrangements to secure your driver to and from the
            hospital for the day of your procedure. Make sure that your
            driver is prepared to wait at the hospital or in the car for
            the duration of your surgery and recovery. They may not leave
            to run errands.
          </li>
          <li>
            Ensure that you have adequate sanitary pads for any discharge
            or bleeding after the procedure.
          </li>
          <li>
            Ensure that you pick up your post-operative pain medications
            from your pharmacy after your surgery is completed (if
            applicable).
          </li>
          <li>
            Schedule your post-operative/follow-up appointment with your
            doctor.
          </li>
          <li>
            Arrive on time for your procedure (typically 2 hours before the
            scheduled surgery start time).
          </li>
          <li>
            Notify our office immediately if you have any concerns. Speak
            to a manager or provider.
          </li>
        </ul>
      </Section>
    </div>
  )
}


function HospitalContactTable() {
  const rows = [
    {
      name: 'University of Maryland Charles Regional Medical Center',
      addr: ['5 Garrett Avenue', 'La Plata, MD 20646'],
      phones: [
        ['General', '301-609-4000'],
        ['Same-Day Surgery', '301-609-4165'],
      ],
    },
    {
      name: 'MedStar Southern Maryland Hospital Center',
      addr: ['7503 Surratts Road', 'Clinton, MD 20735'],
      phones: [['General', '301-868-8000']],
    },
    {
      name: 'VHC Health',
      addr: ['1701 N George Mason Dr', 'Arlington, VA 22205'],
      phones: [['General', '703-558-5000']],
    },
  ]
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
      {rows.map(r => (
        <div key={r.name}
              className="border border-plum-200 rounded-lg p-3 bg-white text-[12px]">
          <div className="font-semibold text-plum-ink">{r.name}</div>
          <div className="text-plum-700/80 mt-1">
            {r.addr.map(l => <div key={l}>{l}</div>)}
          </div>
          <div className="mt-2 space-y-0.5">
            {r.phones.map(([k, v]) => (
              <div key={k}>
                <span className="text-plum-600/70">{k}: </span>
                <a className="underline" href={`tel:${v.replace(/\D/g,'')}`}>{v}</a>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}


function CoordinatorTable() {
  const rows = [
    ["Surgery Coordinator's Direct Number", '240-252-7862'],
    ['Text', 'patient.klara.com (preferred channel of communication)'],
    ['Main Office Number', '240-252-2140 · 703-955-3013'],
    ['Fax', '240-252-2141'],
  ]
  return (
    <div className="border border-plum-200 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-plum-50/60 text-[11px] uppercase tracking-[0.18em] text-plum-700/80 font-semibold border-b border-plum-100">
        Waldorf Women's Care Surgery Coordinator
      </div>
      {rows.map(([k, v], i) => (
        <div key={k}
              className={`grid grid-cols-[150px_1fr] text-[13px] ${
                i ? 'border-t border-plum-100' : ''
              }`}>
          <div className="px-3 py-2 bg-plum-50/40 font-medium text-plum-ink">{k}</div>
          <div className="px-3 py-2 text-plum-ink">{v}</div>
        </div>
      ))}
    </div>
  )
}


function BloodThinnersTable() {
  // Three columns of brand/generic names from the handout — patients are
  // told to confirm timing for any of these with the surgical coordinator.
  const col1 = [
    'Apixaban (Eliquis)',
    'Dabigatran (Pradaxa)',
    'Edoxaban (Savaysa)',
    'Apixaban (Eliquis)',
    'Dabigatran (Pradaxa)',
    'Edoxaban (Savaysa)',
  ]
  const col2 = [
    'Fondaparinux (Arixtra)',
    'Rivaroxaban (Xarelto)',
    'Warfarin (Coumadin, Jantoven)',
    'Aspirin',
    'Clopidogrel (Plavix)',
    'Dipyridamole (Persantine)',
  ]
  const glp1 = [
    'Wegovy (semaglutide)',
    'Zepbound (tirzepatide)',
    'Ozempic (semaglutide)',
    'Saxenda (liraglutide)',
    'Mounjaro (tirzepatide)',
    'Rybelsus (semaglutide)',
    'Victoza (liraglutide)',
    'Trulicity (dulaglutide)',
  ]
  const col3 = [
    <div key="glp1">
      <div className="font-semibold underline">GLP-1s</div>
      <ul className="mt-0.5">
        {glp1.map(g => <li key={g}>{g}</li>)}
      </ul>
    </div>,
    'Heparin (Fragmin, Innohep, and Lovenox)',
    'Dipyridamole (Persantine)',
    'Prasugrel (Effient)',
    'Ticagrelor (Brilinta)',
    'Vorapaxar (Zontivity)',
  ]
  const rows = Array.from({ length: 6 }, (_, i) => [col1[i], col2[i], col3[i]])
  return (
    <div className="border border-plum-200 rounded-lg overflow-hidden text-[12px]">
      {rows.map((r, i) => (
        <div key={i}
              className={`grid grid-cols-3 ${i ? 'border-t border-plum-100' : ''}`}>
          {r.map((cell, j) => (
            <div key={j}
                  className={`px-3 py-2 text-plum-ink ${j < 2 ? 'border-r border-plum-100' : ''}`}>
              {cell}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}


function Section({ title, defaultOpen, tone, children }) {
  const border = tone === 'danger' ? 'border-rose-200' : 'border-plum-100'
  const headBg = tone === 'danger' ? 'bg-rose-50/40' : 'bg-plum-50/40'
  return (
    <details open={!!defaultOpen}
              className={`border ${border} rounded-xl overflow-hidden`}>
      <summary className={`cursor-pointer ${headBg} px-4 py-2 font-serif text-[15px] font-semibold text-plum-ink select-none`}>
        {title}
      </summary>
      <div className="px-4 py-3 space-y-2 bg-white">
        {children}
      </div>
    </details>
  )
}


function SubHead({ children }) {
  return (
    <div className="font-semibold text-plum-ink mt-2 pt-1">
      {children}
    </div>
  )
}


function Warn({ children }) {
  return (
    <div className="text-rose-700 font-medium border-l-4 border-rose-300 pl-3 py-1 bg-rose-50/40 rounded-r">
      {children}
    </div>
  )
}
