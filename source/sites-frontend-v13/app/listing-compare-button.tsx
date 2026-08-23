"use client";

import { useState } from "react";

export default function ListingCompareButton({ variantId }: { variantId: string }) {
  const [message, setMessage] = useState("");

  function add() {
    let current: string[] = [];
    try {
      current = JSON.parse(localStorage.getItem("sa3arly-compare-products") || "[]") as string[];
    } catch {
      current = [];
    }
    if (current.includes(variantId)) {
      setMessage("موجود في المقارنة");
      return;
    }
    if (current.length >= 4) {
      setMessage("الحد الأقصى 4");
      return;
    }
    localStorage.setItem("sa3arly-compare-products", JSON.stringify([...current, variantId]));
    setMessage("تمت الإضافة");
    window.dispatchEvent(new CustomEvent("sa3arly-compare-updated"));
  }

  return (
    <button type="button" className="listing-compare-button" onClick={add}>
      {message || "أضف للمقارنة"}
    </button>
  );
}
