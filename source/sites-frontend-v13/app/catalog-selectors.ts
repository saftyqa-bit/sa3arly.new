import type CatalogDataShape from "./catalog-data.json";

export type CatalogData = typeof CatalogDataShape;
export type Product = CatalogData["products"][number];
