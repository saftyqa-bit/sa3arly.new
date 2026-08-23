export type TaxonomyProduct = {
  section: string;
  type: string;
  subtype: string;
};

export type ProductTaxonomyLeaf = {
  name: string;
  type: string;
  subtype?: string;
};

export type ProductTaxonomyGroup = {
  name?: string;
  items: readonly ProductTaxonomyLeaf[];
};

export type TopCategoryDefinition = {
  slug: string;
  name: string;
  sectionName: string;
  groups: readonly ProductTaxonomyGroup[];
};

export const TOP_CATEGORY_DEFINITIONS = [
  {
    slug: "mobiles",
    name: "الموبايلات والاتصالات",
    sectionName: "الموبايلات والاتصالات",
    groups: [
      {
        items: [
          { name: "هواتف ذكية", type: "هواتف", subtype: "هواتف ذكية" },
          { name: "أجهزة تابلت", type: "أجهزة تابلت" },
          { name: "راوترات منزلية", type: "راوترات منزلية" },
          { name: "ساعات ذكية", type: "ساعات ذكية" },
          {
            name: "هواتف محمولة تقليدية",
            type: "هواتف",
            subtype: "هواتف محمولة تقليدية",
          },
          { name: "باور بانك", type: "باور بانك" },
          { name: "أساور لياقة", type: "أساور لياقة" },
        ],
      },
    ],
  },
  {
    slug: "home-appliances",
    name: "الأجهزة المنزلية",
    sectionName: "الأجهزة المنزلية",
    groups: [
      {
        name: "أجهزة منزلية كبيرة",
        items: [
          { name: "غسالات ملابس", type: "غسالات", subtype: "غسالات ملابس" },
          { name: "ثلاجات", type: "ثلاجات" },
          { name: "بوتاجازات", type: "بوتاجازات" },
          { name: "غسالات أطباق", type: "غسالات", subtype: "غسالات أطباق" },
        ],
      },
      {
        name: "معدات المطبخ",
        items: [
          { name: "قلايات هوائية", type: "قلايات هوائية" },
          { name: "غلايات مياه", type: "غلايات مياه" },
          { name: "ماكينات قهوة تركي", type: "ماكينات قهوة تركي" },
          { name: "ميكروويف", type: "ميكروويف" },
          { name: "خلاطات كهربائية", type: "خلاطات كهربائية" },
          { name: "مطاحن قهوة", type: "مطاحن قهوة" },
          { name: "ماكينات إسبريسو", type: "ماكينات إسبريسو" },
          { name: "خلاطات يدوية", type: "خلاطات يدوية" },
          { name: "محضرات طعام", type: "محضرات طعام" },
          { name: "كبة كهربائية", type: "كبة كهربائية" },
          { name: "ماكينات فرم لحوم", type: "ماكينات فرم لحوم" },
        ],
      },
      {
        name: "أجهزة منزلية صغيرة",
        items: [
          { name: "مكاوي ملابس", type: "مكاوي ملابس" },
          { name: "مكانس كهربائية", type: "مكانس كهربائية" },
          { name: "مراوح عمود", type: "مراوح عمود" },
          { name: "مراوح حائط", type: "مراوح حائط" },
        ],
      },
      {
        name: "تكييفات",
        items: [
          { name: "مكيفات هواء", type: "مكيفات هواء" },
          { name: "أجهزة تنقية الهواء", type: "أجهزة تنقية الهواء" },
        ],
      },
    ],
  },
  {
    slug: "kitchen",
    name: "المطبخ والأدوات المنزلية",
    sectionName: "المطبخ والأدوات المنزلية",
    groups: [
      {
        name: "حلل وأواني الطهي",
        items: [
          { name: "حلل طهي", type: "حلل طهي" },
          { name: "طاسات", type: "طاسات" },
          { name: "أطقم أواني طهي", type: "أطقم أواني طهي" },
          { name: "أطباق فرن", type: "أطباق فرن" },
          { name: "أواني طهي", type: "أواني طهي" },
          { name: "طاسات سوتيه", type: "طاسات سوتيه" },
          { name: "حلل صوص", type: "حلل صوص" },
          { name: "حلل ضغط", type: "حلل ضغط" },
          {
            name: "حلل مكرونة ومتعددة الاستخدام",
            type: "حلل مكرونة وحلل متعددة الاستخدام",
          },
        ],
      },
      {
        name: "التقديم والسفرة",
        items: [
          { name: "أوعية تقديم", type: "أوعية تقديم" },
          { name: "صواني وسلال تقديم", type: "صواني وسلال تقديم" },
          { name: "أطباق منفردة", type: "أطباق منفردة" },
          { name: "أدوات تقديم", type: "أدوات تقديم" },
          { name: "قوارير تقديم", type: "قوارير تقديم" },
          { name: "أدوات سفرة", type: "أدوات سفرة" },
          { name: "ملاعق سفرة", type: "ملاعق سفرة" },
          { name: "قواعد أكواب", type: "قواعد أكواب" },
          { name: "حوامل وعلب توابل", type: "حوامل وعلب توابل" },
        ],
      },
      {
        name: "حفظ وتخزين الطعام",
        items: [
          { name: "علب حفظ طعام", type: "علب حفظ طعام" },
          { name: "حفظ وتنظيم الطعام", type: "حفظ وتنظيم الطعام" },
          { name: "أوعية حفظ", type: "أوعية حفظ" },
          { name: "برطمانات حفظ", type: "برطمانات حفظ" },
        ],
      },
      {
        name: "أكواب وزجاجات وأباريق",
        items: [
          { name: "زجاجات مياه ومشروبات", type: "زجاجات مياه ومشروبات" },
          { name: "مجات وأكواب", type: "مجات وأكواب" },
          { name: "أكواب مياه", type: "أكواب مياه" },
          { name: "أباريق", type: "أباريق" },
          { name: "زجاجات مياه", type: "زجاجات مياه" },
          { name: "كؤوس", type: "كؤوس" },
          { name: "أباريق شاي", type: "أباريق شاي" },
        ],
      },
      {
        name: "أدوات المطبخ والتحضير",
        items: [
          { name: "مصافي مطبخ", type: "مصافي مطبخ" },
          { name: "أدوات مطبخ", type: "أدوات مطبخ" },
          {
            name: "قفازات وحوامل أواني ساخنة",
            type: "قفازات وحوامل أواني ساخنة",
          },
          { name: "قواعد وحوامل أواني", type: "قواعد وحوامل أواني" },
          { name: "ملاعق تقليب", type: "ملاعق تقليب" },
          { name: "أدوات خبز", type: "أدوات خبز" },
          { name: "قواطع عجين", type: "قواطع عجين" },
          { name: "مضارب يدوية", type: "مضارب يدوية" },
          { name: "أطقم أدوات مطبخ", type: "أطقم أدوات مطبخ" },
          { name: "مكاييل مطبخ", type: "مكاييل مطبخ" },
          { name: "فرش معجنات", type: "فرش معجنات" },
          { name: "مغارف", type: "مغارف" },
          { name: "مضارب حليب", type: "مضارب حليب" },
          { name: "مقشرات", type: "مقشرات" },
          { name: "مبشرات", type: "مبشرات" },
          { name: "أغطية أواني ميكروويف", type: "أغطية أواني ميكروويف" },
        ],
      },
      {
        name: "أدوات التنظيف والقمامة",
        items: [
          { name: "فوط تنظيف", type: "فوط تنظيف" },
          { name: "سلال قمامة وفرز", type: "سلال قمامة وفرز" },
          { name: "أدوات تنظيف", type: "أدوات تنظيف" },
          { name: "أدوات غسيل أطباق", type: "أدوات غسيل أطباق" },
          {
            name: "مصافي وحوامل تجفيف أطباق",
            type: "مصافي وحوامل تجفيف أطباق",
          },
          { name: "سلال وأكياس قمامة", type: "سلال وأكياس قمامة" },
          { name: "فرش وإسفنج تنظيف", type: "فرش وإسفنج تنظيف" },
          { name: "جرادل", type: "جرادل وأدوات تنظيف" },
          { name: "مكانس يدوية وجواريف", type: "مكانس يدوية وجواريف" },
          { name: "موزعات صابون", type: "موزعات صابون مطبخ" },
          { name: "قفازات تنظيف", type: "قفازات تنظيف" },
          { name: "مماسح أرضيات", type: "مماسح أرضيات" },
        ],
      },
      {
        name: "منسوجات المطبخ",
        items: [
          { name: "فوط مطبخ", type: "فوط مطبخ" },
          { name: "مرايل مطبخ", type: "مرايل مطبخ" },
          { name: "منسوجات مطبخ", type: "منسوجات مطبخ" },
        ],
      },
      {
        name: "السكاكين وأدوات التقطيع",
        items: [
          { name: "سكاكين مطبخ", type: "سكاكين مطبخ" },
          { name: "ألواح تقطيع", type: "ألواح تقطيع" },
          { name: "أطقم سكاكين مطبخ", type: "أطقم سكاكين مطبخ" },
          { name: "سكاكين وألواح تقطيع", type: "سكاكين وألواح تقطيع" },
        ],
      },
      {
        name: "أدوات القهوة والشاي",
        items: [
          { name: "أدوات قهوة وشاي", type: "أدوات قهوة وشاي" },
          { name: "أدوات تحضير قهوة وشاي", type: "أدوات تحضير قهوة وشاي" },
        ],
      },
    ],
  },
  {
    slug: "tv-audio",
    name: "التلفزيونات والصوتيات والتصوير",
    sectionName: "التلفزيونات والصوتيات والتصوير",
    groups: [
      {
        name: "تليفزيونات وأجهزة عرض",
        items: [
          { name: "تلفزيونات", type: "تلفزيونات", subtype: "" },
          { name: "بروجكتورات منزلية", type: "بروجكتورات منزلية" },
          {
            name: "أجهزة بث تلفزيوني",
            type: "تلفزيونات",
            subtype: "أجهزة بث تلفزيوني",
          },
        ],
      },
      {
        name: "الصوتيات والسماعات",
        items: [
          { name: "سماعات رأس", type: "سماعات", subtype: "سماعات رأس" },
          {
            name: "سماعات بلوتوث محمولة",
            type: "سماعات",
            subtype: "سماعات بلوتوث محمولة",
          },
          { name: "ساوند بار وأنظمة صوت منزلي", type: "ساوند بار وأنظمة صوت منزلي" },
        ],
      },
      {
        name: "الكاميرات والتصوير",
        items: [
          { name: "كاميرات أكشن", type: "كاميرات", subtype: "كاميرات أكشن" },
          { name: "كاميرات رقمية", type: "كاميرات", subtype: "كاميرات رقمية" },
        ],
      },
      {
        name: "أنظمة الاتصال الداخلي",
        items: [{ name: "أجهزة إنتركم", type: "أجهزة إنتركم" }],
      },
    ],
  },
  {
    slug: "computers",
    name: "الكمبيوتر والشبكات",
    sectionName: "الكمبيوتر والشبكات",
    groups: [
      {
        name: "لاب توب",
        items: [{ name: "لاب توب", type: "لابتوبات" }],
      },
      {
        name: "مكونات الكمبيوتر",
        items: [
          { name: "لوحات أم", type: "لوحات أم" },
          { name: "بطاقات رسومية", type: "بطاقات رسومية" },
        ],
      },
      {
        name: "شاشات كمبيوتر",
        items: [{ name: "شاشات كمبيوتر", type: "شاشات كمبيوتر" }],
      },
      {
        name: "أجهزة الشبكات",
        items: [
          { name: "محولات شبكة USB", type: "محولات شبكة USB" },
          { name: "نقاط وصول لاسلكية", type: "نقاط وصول لاسلكية" },
        ],
      },
    ],
  },
  {
    slug: "gaming",
    name: "الألعاب الإلكترونية",
    sectionName: "الألعاب الإلكترونية",
    groups: [
      {
        name: "إكسسوارات الجيمينج",
        items: [
          { name: "سماعات ألعاب", type: "سماعات", subtype: "سماعات ألعاب" },
          { name: "فأرات ألعاب", type: "فأرات ألعاب" },
          { name: "أذرع تحكم وملحقات كونسول", type: "أذرع تحكم وملحقات كونسول" },
        ],
      },
      {
        name: "أجهزة الألعاب",
        items: [
          { name: "أجهزة ألعاب منزلية", type: "أجهزة ألعاب منزلية" },
          { name: "أجهزة ألعاب محمولة", type: "أجهزة ألعاب محمولة" },
          { name: "نظارات واقع افتراضي", type: "نظارات واقع افتراضي" },
        ],
      },
      {
        name: "ألعاب وأقراص",
        items: [
          { name: "ألعاب فيديو على أقراص", type: "ألعاب فيديو على أقراص" },
          { name: "ألعاب فيديو", type: "ألعاب فيديو" },
        ],
      },
    ],
  },
  {
    slug: "toys-hobbies",
    name: "الألعاب والهوايات",
    sectionName: "الألعاب والهوايات",
    groups: [
      {
        items: [
          { name: "نماذج تركيب", type: "نماذج تركيب" },
          { name: "مجسمات وشخصيات قابلة للجمع", type: "مجسمات وشخصيات قابلة للجمع" },
          { name: "بلابل قتالية", type: "بلابل قتالية" },
          { name: "مكعبات بناء", type: "مكعبات بناء وألعاب تركيب" },
          { name: "بازل", type: "بازل" },
          { name: "بطاقات مقتنيات", type: "بطاقات مقتنيات" },
          { name: "بطاقات وألعاب طاولة", type: "بطاقات وألعاب طاولة" },
        ],
      },
    ],
  },
  {
    slug: "baby",
    name: "الأطفال والأمومة",
    sectionName: "الأطفال والأمومة",
    groups: [
      {
        items: [
          { name: "عربات أطفال", type: "عربات أطفال" },
          { name: "ببرونات وحلمات رضاعة", type: "ببرونات وحلمات رضاعة" },
          { name: "مقاعد سيارة للأطفال", type: "مقاعد سيارة للأطفال" },
          { name: "شفاطات حليب", type: "شفاطات حليب" },
          { name: "أنظمة سفر للرضع", type: "أنظمة سفر للرضع" },
          { name: "أسرة ومفروشات أطفال", type: "أسرة ومفروشات أطفال" },
        ],
      },
    ],
  },
] as const satisfies readonly TopCategoryDefinition[];

export function topCategoryDefinitionForSection(
  sectionName: string,
): TopCategoryDefinition | null {
  return (
    TOP_CATEGORY_DEFINITIONS.find(
      (category) => category.sectionName === sectionName,
    ) ?? null
  );
}

export function taxonomyLeaves(category: TopCategoryDefinition) {
  return category.groups.flatMap((group) => group.items);
}

export function productMatchesTaxonomyLeaf(
  product: TaxonomyProduct,
  leaf: ProductTaxonomyLeaf,
) {
  return (
    product.type === leaf.type &&
    (leaf.subtype === undefined || product.subtype === leaf.subtype)
  );
}

export function taxonomyLeafForProduct(product: TaxonomyProduct) {
  const category = topCategoryDefinitionForSection(product.section);
  return (
    category?.groups
      .flatMap((group) => group.items)
      .find((leaf) => productMatchesTaxonomyLeaf(product, leaf)) ?? null
  );
}

export function taxonomyLeafCount(
  products: readonly TaxonomyProduct[],
  leaf: ProductTaxonomyLeaf,
) {
  return products.filter((product) => productMatchesTaxonomyLeaf(product, leaf))
    .length;
}
