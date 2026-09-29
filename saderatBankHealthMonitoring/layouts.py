"""What each charted monitoring's spreadsheet is read for.

`id_column` is required: without it no one in the file can be opened.
`chart_columns` only feed charts; a missing one leaves that chart empty,
so it is reported, never refused.
"""

LAYOUTS = {
    'step_1': {
        'id_column': 'personel.کد ملی',
        'chart_columns': (
            'Alkaline Phosphatase', 'BMI', 'BMI_Group', 'BP_Group', 'CBC/Hb', 'CBC/Hct',
            'CBC/MCH', 'CBC/MCHC', 'CBC/MCV', 'CBC/Plat', 'CBC/RBC', 'CBC/WBC', 'Cr', 'FBS',
            'Ferritin', 'HDL', 'Hb-A1C', 'K', 'LDL', 'Na', 'P', 'PSA', 'SGOT(AST)', 'SGPT(ALT)',
            'T3', 'T4', 'TG', 'TSH', 'Total Chol', 'U_A/Bact', 'U_A/Blood', 'U_A/Glu',
            'U_A/Ketone', 'U_A/Prot', 'U_A/RBC', 'U_A/WBC', 'U_A/crystal', 'Urea', 'Vit D',
            'bilirubin-direct', 'ca', 'name_goroh', 'vitamin b12', 'اندوکرینولوژی', 'بيمه',
            'بیماریهای عضلانی قلب', 'تفسیر الکتروکاردیوگرام', 'تناسلی مردان', 'جنسیت',
            'رادیوگرافی قفسه سینه', 'روماتولوژی', 'سایکولوژی', 'ستون فقرات پشتی و کمری',
            'سر و گردن', 'سن', 'سونوگرافی شکم و لگن', 'سیستم تنفسی', 'قلب', 'مشاوره قلب',
            'معاینات بالینی زنان', 'معاینه بالینی ENT', 'نام صنعت', 'نبض', 'نورولوژی',
            'هماتولوژی', 'پاپ اسمیر', 'پستان', 'گوارش',
        ),
    },
    'step_2': {
        'id_column': 'کد ملی',
        'chart_columns': (
            'Heart rate:', 'Respiratory rate', 'آزمایشات تکمیلی مورد نیاز',
            'آيا دارو خاصي مصرف مي كنيد؟ذكرنماييد.',
            'آيا سابقه بيماري ارثي درخانواده داريد ؟نام  ببريد.',
            'آيا سابقه عمل جراحي داريد ؟ذكر نمايد.', 'آيا سيگارميكشيد؟', 'اندوكرينولوژي',
            'جنسیت', 'روماتولوژي', 'ستون فقرات پشتی و کمری', 'سر و گردن', 'سيستم تنفسي',
            'سيستم عضلاني اسكلتي تحتاني', 'سيستم عضلاني اسكلتي فوقان', 'علائم عمومي',
            'عوامل  رواني', 'عوامل ارگونوميك', 'قلب', 'نورولوژی', 'هماتولوژي', 'پستان',
            'پوست و  مو', 'گوارش',
        ),
    },
}
