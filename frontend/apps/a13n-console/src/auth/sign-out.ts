import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useAuth } from "./context";

export function useSignOut() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: logout,
    onSuccess: () => navigate("/login", { replace: true }),
  });
}
