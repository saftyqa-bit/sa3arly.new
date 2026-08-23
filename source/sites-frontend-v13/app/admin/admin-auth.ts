import { getChatGPTUser, type ChatGPTUser } from "../chatgpt-auth";

function configuredAdminEmails() {
  return new Set(
    String(process.env.SA3ARLY_ADMIN_EMAILS ?? "")
      .split(",")
      .map((value) => value.trim().toLocaleLowerCase())
      .filter(Boolean),
  );
}

export async function getSa3arlyAdmin(): Promise<ChatGPTUser | null> {
  const user = await getChatGPTUser();
  const developmentEmail =
    process.env.NODE_ENV !== "production"
      ? process.env.SA3ARLY_DEV_ADMIN_EMAIL?.trim()
      : null;
  const resolvedUser =
    user ??
    (developmentEmail
      ? {
          email: developmentEmail,
          displayName: "Sa3arly Developer",
          fullName: "Sa3arly Developer",
        }
      : null);
  if (!resolvedUser) return null;

  const allowed = configuredAdminEmails();
  if (allowed.size === 0) {
    return process.env.NODE_ENV !== "production" ? resolvedUser : null;
  }
  return allowed.has(resolvedUser.email.toLocaleLowerCase())
    ? resolvedUser
    : null;
}
