/**
 * Full text of the WWC "Preparing for Your Upcoming Office-Based Procedure"
 * handout, rendered inline (no PDF download). Each major section is a
 * collapsible <details> block so the page doesn't overwhelm.
 */
export default function OfficeProcedureInstructions() {
  return (
    <div className="space-y-4 text-[13px] leading-relaxed text-plum-ink">
      <header className="text-center pb-2 border-b border-plum-100">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium">
          Waldorf Women's Care · WWC Gynecology &amp; Aesthetics
        </div>
        <h3 className="font-serif text-[18px] text-plum-ink font-semibold tracking-tight mt-1">
          Preparing for Your Upcoming Office-Based Procedure
        </h3>
      </header>

      <p>Thank you for entrusting your medical and surgical care to Waldorf Women's Care.</p>
      <p>
        Preparing for your procedure can be a stressful process. It requires
        careful coordination between you, our office, and your insurance
        company. We do our best to make the process as easy as possible. Be
        sure to do your part to make the process as smooth as possible.
      </p>
      <p>
        This document will help to guide you through this process. If any
        aspect of the process is unclear, please Klara Secure Text{' '}
        <a className="underline" href="sms:2409291907">240-929-1907</a> or call your
        Waldorf Women's Care surgery coordinator at{' '}
        <a className="underline" href="tel:2402527862">240-252-7862</a> or{' '}
        <a className="underline" href="tel:2402522140">240-252-2140</a> and she will
        gladly work with you to resolve the issue.
      </p>

      <ContactTable />

      <Section title="Important Notices" defaultOpen>
        <SubHead>Insurance and Financial Responsibility</SubHead>
        <p>
          Your financial responsibility is a calculation of your deductible,
          copay, and/or coinsurance. You must pay your financial
          responsibility before securing a surgical date. We encourage our
          patients to call their insurance company to verify their financial
          responsibility prior to surgery (the information is on the back of
          your insurance card). They will need the surgery CPT code and the
          diagnosis code(s), also known as ICD-10 codes. If you are unable to
          make a full payment, contact your surgery coordinator to ask about
          a payment plan.
        </p>

        <SubHead>Referral</SubHead>
        <p>
          You will be notified if your insurance requires a referral from
          your primary care provider to be covered for your procedure.
          Waldorf Women's Care would like to have a copy of this referral one
          week before surgery. In the event you need a referral from your
          primary care provider for your procedure, please call the primary
          care doctor's office and let them know what surgery you are
          scheduled for, as well as why you are having surgery, and explain
          that you require the referral for insurance to cover your
          procedure. If you are having difficulty obtaining this referral,
          you need to contact your surgical coordinator right away. If we do
          not receive this referral, your surgery may be cancelled.
        </p>

        <SubHead>Cancellations &amp; Reschedules</SubHead>
        <p>
          All cancellations and reschedules must be made with the Waldorf
          Women's Care/WWC Gynecology &amp; Aesthetics surgery coordinator
          (the contact information is listed in the above table).
        </p>
        <Warn>
          You may only cancel by contacting the surgery coordinator by
          texting <a className="underline" href="sms:2409291907">240-929-1907</a>,
          calling <a className="underline" href="tel:2402527862">240-252-7862</a> or
          emailing{' '}
          <a className="underline" href="mailto:surgery@waldorfwomenscare.com">
            surgery@waldorfwomenscare.com
          </a>.
        </Warn>
        <p>
          There is a lot that goes into scheduling your procedure, so if a
          procedure must be canceled, the surgery coordinator must be
          contacted immediately. The surgical coordinator will, in turn,
          alert your doctor, and any other necessary entity. Waldorf
          Women's Care/WWC Gynecology &amp; Aesthetics reserves the right to
          charge a fee of <strong>$351</strong> for all surgeries/procedures
          not canceled within <strong>14 days</strong> in advance, absent a
          compelling reason, and at our sole discretion. This fee is not
          covered by insurance and must be paid before scheduling your next
          appointment.
        </p>

        <SubHead>Designated Driver</SubHead>
        <p>
          Please make sure that you have a designated driver who is{' '}
          <strong>18 years old or older</strong> to take you to and from our
          office for your procedure.
        </p>
        <Warn>
          YOU MAY <u>NOT</u> DRIVE YOURSELF HOME AFTER THE PROCEDURE AND
          YOUR DRIVER MUST REMAIN IN THE OFFICE OR PARKING LOT DURING YOUR
          PROCEDURE. YOU MAY <u>NOT</u> USE ANY RIDE-SHARING SERVICE
          (UBER/LYFT/TAXI) AS TRANSPORTATION.
        </Warn>

        <SubHead>Current Medications</SubHead>
        <p>
          Please continue taking your regular medication(s) as instructed.
          However, if you are taking blood thinners or aspirin, please
          discontinue using these medications as instructed by your doctor
          as they may lead to increased blood loss during your procedure.
        </p>

        <SubHead>Menstrual bleeding at the time of your procedure</SubHead>
        <p>
          Your procedure can be performed during your menstruation. However,
          if you are having a LEEP, schedule the procedure outside of your
          normal menstrual cycle.
        </p>

        <SubHead>Your Pre-op Appointment</SubHead>
        <p>
          The provider will discuss your procedure in detail. The provider
          will also review the risks, possible complications, and your
          recovery period. At the end of your appointment, your provider
          will answer any questions. You will receive a text message from
          our "Klara" secure messaging system (240-929-1907) to pay your
          surgical responsibility and to choose your surgical date. If your
          pre-op appointment is in the office, you will sign the surgical
          consent during your visit. However, if your pre-op appointment is
          virtual, you will need to sign your surgical consent via DocuSign
          or your patient portal.
        </p>
      </Section>

      <Section title="Preparation Timeline — MUST READ" defaultOpen>
        <p>
          You were electronically prescribed the following medications for
          your procedure. They were sent to your pharmacy on file.
        </p>
        <Warn>Pick up these prescriptions as soon as possible!</Warn>

        <ul className="list-disc pl-5 space-y-2">
          <li>
            <strong><u>All Procedures except LEEP</u>:</strong> Cytotec
            (Misoprostol) 200mcg to soften your cervix (you will be
            prescribed 4 pills)
            <ul className="list-[circle] pl-5 mt-1 space-y-0.5">
              <li>1st tablet: Take 1 tablet at 7 am the day before the procedure</li>
              <li>2nd tablet: Take 1 tablet 6 hours after the first tablet</li>
              <li>3rd tablet: Take 1 tablet right before bedtime</li>
              <li>4th tablet: Take 1 tablet before 7:00 am on the morning of the procedure</li>
            </ul>
          </li>
          <li>
            <strong>Zofran (Ondansetron) 4mg</strong> for nausea you may
            experience before or after your procedure
            <ul className="list-[circle] pl-5 mt-1">
              <li>Take one 4mg tab 1 hour before your scheduled arrival time.
                If needed, you may take another dose after your procedure.</li>
            </ul>
          </li>
          <li>
            <strong>Percocet (Oxycodone/Acetaminophen) 5/325mg</strong> for pain relief
            <ul className="list-[circle] pl-5 mt-1">
              <li>Take the prescribed dose 1 hour before your scheduled arrival time.</li>
            </ul>
          </li>
          <li>
            <strong>Valium (Diazepam) 10mg</strong> to relax you
            <ul className="list-[circle] pl-5 mt-1">
              <li>Take the prescribed dose 1 hour before your scheduled arrival time.</li>
            </ul>
          </li>
          <li>
            <strong>Ibuprofen 600mg</strong> (purchase over the counter)
            <ul className="list-[circle] pl-5 mt-1">
              <li>Take 600mg (3 × 200mg) before bedtime the night before the
                procedure <strong>UNLESS</strong> you have been instructed{' '}
                <strong>NOT</strong> to take ibuprofen by your surgeon or
                medical doctor.</li>
            </ul>
          </li>
        </ul>

        <SubHead>10–14 Days BEFORE Your Procedure</SubHead>
        <ol className="list-decimal pl-5 space-y-1">
          <li>Attend pre-operative clearance appointments with either a
            Primary Care Physician (PCP) or Internal Medicine Provider, if
            required by your surgeon.</li>
          <li>Notify your surgeon if you could be pregnant (at any point
            leading up to your procedure).</li>
          <li>Schedule a follow-up appointment with your physician for
            approximately two (2) weeks after the procedure. This may be a
            virtual appointment unless otherwise instructed by your surgeon.</li>
          <li>Pick up all prescribed medications from your pharmacy.</li>
        </ol>

        <SubHead>24 Hours BEFORE Your Procedure</SubHead>
        <ul className="list-disc pl-5 space-y-1">
          <li>Start taking the Cytotec tablets as written on the prescription.
            We recommend starting Cytotec in the morning and continuing
            throughout the day as explained above. This medication may cause
            spotting, cramping, or diarrhea in some patients.{' '}
            <em>(NOT for LEEP procedure.)</em></li>
          <li>Although you should have filled all your prescriptions before
            today, verify that you have all the medications.</li>
          <li>Take Ibuprofen 600mg (3 × 200mg tabs) at bedtime{' '}
            <strong>UNLESS</strong> you have been instructed{' '}
            <strong>NOT</strong> to take ibuprofen by your surgeon or
            medical doctor.</li>
          <li className="text-rose-700 font-medium">
            Confirm that you have RELIABLE transportation to and from the
            office. Your transportation must remain in the office or parking
            lot during your procedure.
          </li>
          <li>Drink at least 64 oz of water the day before your procedure.</li>
        </ul>

        <SubHead>Day of Your Procedure</SubHead>
        <ol className="list-decimal pl-5 space-y-1">
          <li>Make sure you have eaten a full meal about 2 hours before your
            procedure. If you have a sensitive stomach and react with nausea
            when taking pain medication, we suggest you eat a light, bland
            meal (soup, sandwich, rice) before coming in. Also drink plenty
            of water throughout the day.</li>
          <li>Take your last Cytotec tablet before 7:00 am.</li>
          <li>Take Zofran{' '}
            <span className="text-rose-700 font-medium">
              1 hour before your scheduled arrival time
            </span>.
          </li>
          <li>Take the Valium and Percocet with water{' '}
            <span className="text-rose-700 font-medium">
              1 hour before your scheduled arrival time
            </span>.
          </li>
          <li>Wear loose-fitting, comfortable clothing.</li>
          <li>Plan to be in the office for up to 2 hours.</li>
        </ol>

        <SubHead>Upon Arrival at the Office</SubHead>
        <ul className="list-disc pl-5 space-y-1">
          <li>Check in at the front desk.</li>
          <li>You will be asked to leave a urine specimen for a pregnancy
            test. This is required for all procedures unless you are in
            menopause.</li>
          <li>You will be asked to sit in the lobby to await your procedure.
            The medications you have taken reach peak effectiveness between
            1 hour and 4 hours after taking them. The effects of the
            medication may last for up to 6 hours.</li>
          <li>The medical assistant will escort you to a changing area to
            change into your gown.</li>
          <li>After you have changed, the medical assistant will then
            escort you to the procedure room, where you will sit in the
            procedure chair.</li>
        </ul>

        <SubHead>During Your Procedure</SubHead>
        <ol className="list-decimal pl-5 space-y-1">
          <li>We will play music to help you relax during the procedure.</li>
          <li>You will sit in the procedure chair, and your feet will be
            placed in stirrups as for any pelvic exam. The assistant will
            cleanse the vagina as well as your lower pelvic area with
            Betadine or Hibiclens.</li>
          <li>Before performing the procedure, a paracervical block
            (injection of a local anesthetic around the cervix) will be
            administered. You will feel a sting as the anesthetic is being
            administered. The physician will wait for 5–10 minutes for the
            anesthetic to take effect before starting the procedure. The
            procedure will take about 15–30 minutes.</li>
        </ol>

        <SubHead>After Your Procedure</SubHead>
        <ul className="list-disc pl-5 space-y-1">
          <li>You will be asked to rest for a few minutes after the procedure
            is completed.</li>
          <li>When you feel ready, we will wheel you to your car, and you
            will be discharged to the care of your responsible party.</li>
          <li>You will have a watery and/or bloody discharge following the
            procedure, which may persist for several weeks after your
            procedure. You may also experience some cramping, spotting, or
            bleeding. The spotting may last for several weeks.</li>
          <li>You may feel dizzy and tired for several hours after the
            procedure; plan to go home and rest for the remainder of the day.</li>
          <li>Use a heating pad or hot water bottle as needed for cramps.</li>
          <li>You may resume your regular diet.</li>
          <li>You may take over-the-counter Ibuprofen 200mg (up to 3 tablets)
            every six hours for the first 24 hours after the procedure;
            after 24 hours, take only as needed. If you have an allergy or
            cannot take Ibuprofen, you may take Tylenol 650mg or 1000mg per
            instructions on the medication bottle.</li>
          <li>Abstain from intercourse for two (2) weeks.</li>
          <li>Do not insert <u>anything</u> into the vagina (including
            tampons) for two (2) weeks.</li>
          <li>Do not submerge your vagina in water for two (2) weeks
            (no sit-down baths or swimming). You may shower.</li>
          <li>You may return to <u>all</u> of your other normal activities
            within one to two days after the procedure, including driving
            and returning to your normal level of exercise.</li>
        </ul>

        <SubHead>Two (2) Weeks After Your Procedure</SubHead>
        <ul className="list-disc pl-5 space-y-1">
          <li>Meet with your provider for your post-operative appointment to
            review the operative findings and any results. This may be a
            virtual or in-person appointment.</li>
          <li>After being cleared by the provider, you may insert objects
            into your vagina (you may resume intercourse and tampon use).</li>
          <li>After being cleared by the provider, you may submerge your
            vaginal area under water (you may resume swimming and sit-down
            baths).</li>
        </ul>
      </Section>

      <Section title="Call Our Office If You Experience These Symptoms" defaultOpen tone="danger">
        <p className="text-rose-700 font-medium">
          Contact our office at <a className="underline" href="tel:2402522140">240-252-2140</a>.
        </p>
        <ul className="list-disc pl-5 space-y-1">
          <li>If you are calling <strong>after hours</strong>, choose option 7 to reach the
            answering service, which will contact the provider on call.</li>
          <li>If you are calling <strong>during business hours</strong>, ask to speak to a
            manager or provider if you are experiencing any of the following:</li>
        </ul>
        <div className="border border-rose-200 rounded-lg p-3 bg-rose-50/40">
          <ul className="list-disc pl-5 space-y-1 text-rose-700 font-medium">
            <li>Cramping or pain not controlled with Ibuprofen or Tylenol.{' '}
              <span className="text-emerald-700 font-normal">
                (Cramping for up to 1 week post-procedure is normal.)
              </span></li>
            <li>Heavy vaginal bleeding — soaking a pad every one to two hours.{' '}
              <span className="text-emerald-700 font-normal">
                (Some spotting or light bleeding for several weeks is normal.)
              </span></li>
            <li>Fever over 100.4°F.</li>
            <li>Foul-smelling vaginal discharge.</li>
            <li>Nausea or vomiting.</li>
            <li>Have not urinated within 6 hours after arriving home.</li>
            <li>Chest pain.</li>
            <li>Shortness of breath.</li>
            <li>Swelling of the face and tongue.</li>
            <li>Suicidal or homicidal thoughts.</li>
          </ul>
        </div>
        <p className="text-center text-rose-700 font-semibold pt-1">
          In a life-threatening emergency, call <a className="underline" href="tel:911">911</a>{' '}
          or go to the nearest hospital emergency department.
        </p>
      </Section>

      <Section title="Checklist for a Successful and Smooth Experience">
        <ul className="list-disc pl-5 space-y-1">
          <li>If I'm having a NovaSure (Endometrial Ablation), I have confirmed
            that the result of my pathology report from my D&amp;C or
            Endometrial Biopsy was normal.</li>
          <li>Complete pre-op appointment.</li>
          <li>Sign your procedure consent.</li>
          <li>Pay your estimated financial responsibility. You may call your
            insurance to verify your patient responsibility.</li>
          <li>Schedule your procedure.</li>
          <li>Schedule the day off from work.</li>
          <li>Make arrangements for your children/dependents.</li>
          <li>Notify your loved ones of your procedure so they may assist you
            during your recovery period.</li>
          <li>Make arrangements to secure your driver to and from the office
            for the day of your procedure. Make sure that your driver is
            prepared to wait at the office or in the car for 2 hours or so.
            They may not leave to run errands.</li>
          <li>Pick up your prescribed medications (read and follow the
            instructions on the bottle carefully).</li>
          <li>Ensure that you have adequate sanitary pads for any discharge
            or bleeding after the procedure.</li>
          <li>Ensure that you have several doses of Tylenol or Ibuprofen on
            hand for pain and cramps.</li>
          <li>Schedule your post-operative/follow-up appointment with your
            doctor.</li>
          <li>Arrive on time for your procedure.</li>
          <li>Notify our office immediately if you have any concerns. Speak
            to a manager or provider.</li>
        </ul>
      </Section>
    </div>
  )
}


function ContactTable() {
  const rows = [
    ['Klara Secure Text', '240-929-1907'],
    ['Phone',             '240-252-7862 or 240-252-2140, Option 9'],
    ['Email',             'surgery@waldorfwomenscare.com'],
    ['After Hours',       '240-252-2140, Option 7 (answering service)'],
  ]
  return (
    <div className="border border-plum-200 rounded-lg overflow-hidden">
      {rows.map(([k, v], i) => (
        <div key={k}
              className={`grid grid-cols-[150px_1fr] text-[13px] ${
                i ? 'border-t border-plum-100' : ''
              }`}>
          <div className="px-3 py-2 bg-plum-50/60 font-medium text-plum-ink">{k}</div>
          <div className="px-3 py-2 text-plum-ink">{v}</div>
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
