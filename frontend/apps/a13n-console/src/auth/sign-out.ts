import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useAuth } from "./context";

export function useSignOut() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: logout,
    onSuccess: (logoutUrl) => {
      if (logoutUrl) window.location.assign(logoutUrl);
      else navigate("/login", { replace: true });
    },
  });
}
