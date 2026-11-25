import { afterEach, expect, it, vi } from "vitest";
import { useSignOut } from "./sign-out";

const { logout, navigate } = vi.hoisted(() => ({
  logout: vi.fn<() => Promise<string | undefined>>(),
  navigate: vi.fn(),
}));
vi.mock("./context", () => ({ useAuth: () => ({ logout }) }));
vi.mock("react-router", () => ({ useNavigate: () => navigate }));
vi.mock("@tanstack/react-query", () => ({
  useMutation: ({
    mutationFn,
    onSuccess,
  }: {
    mutationFn: typeof logout;
    onSuccess: (url: string | undefined) => void;
  }) => ({ mutateAsync: async () => onSuccess(await mutationFn()) }),
}));
afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

it.each([undefined, "https://identity.example.test/logout"])(
  "opens the logout destination (%s) after the session ends",
  async (url) => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { assign } });
    let finish!: (url: string | undefined) => void;
    logout.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const pending = useSignOut().mutateAsync();
    expect(assign).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    finish(url);
    await pending;
    if (url) {
      expect(assign).toHaveBeenCalledWith(url);
      expect(navigate).not.toHaveBeenCalled();
    } else {
      expect(assign).not.toHaveBeenCalled();
      expect(navigate).toHaveBeenCalledWith("/login", { replace: true });
    }
  },
);
